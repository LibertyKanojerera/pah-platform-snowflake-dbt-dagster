"""Incremental extraction from the application Postgres source.

Watermarks on `created_at`, never on `event_timestamp`. See ADR-018: a
business timestamp as a watermark silently drops late-arriving rows, and the
loss is invisible because nothing errors.

Two mechanisms implement that decision and both matter:

* **Safety lag.** The upper bound of each read is `now() - SAFETY_LAG`,
  computed by the database rather than the client. PostgreSQL's `now()` is the
  transaction *start* time, so a long transaction can commit rows stamped
  behind a watermark that has already moved past them. Any transaction shorter
  than the lag is safely captured.
* **The watermark advances to max(created_at) of rows actually landed**, not
  to the query's upper bound. If nothing was read, it does not advance at all.
  Advancing to the bound would skip the gap between the last row seen and the
  bound, which is exactly where a late commit lands.

Both tables are extracted independently with their own checkpoints, because
they change at different rates and a failure in one should not stall the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from ingest.core.config import get_postgres_config
from ingest.core.landing import Batch, LandedPart, LandingWriter
from ingest.core.log import get_logger
from ingest.core.runlog import RunLog
from ingest.core.state import StateStore

log = get_logger(__name__)

#: Transactions shorter than this are safely captured. See ADR-018.
SAFETY_LAG = "1 minute"

#: Rows per Parquet part. 77,000 events fit in memory comfortably, but
#: chunking keeps the extractor's footprint independent of the source size.
PART_SIZE = 50_000

#: Server-side cursor fetch size. Keeps the client from buffering the whole
#: result set, which a plain cursor would do.
CURSOR_ITERSIZE = 10_000

EPOCH = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class SourceTable:
    name: str
    key_column: str

    @property
    def source_id(self) -> str:
        """Checkpoint key and landing-zone source name."""
        return f"app_postgres_{self.name}"


TABLES = (
    SourceTable(name="patients", key_column="patient_id"),
    SourceTable(name="patient_events", key_column="event_id"),
)


def _dsn() -> str:
    config = get_postgres_config()
    return (
        f"host={config.host} port={config.port} dbname={config.dbname} "
        f"user={config.user} password={config.password.get_secret_value()}"
    )


def _iter_rows(
    conn: psycopg.Connection, table: SourceTable, watermark: str
) -> Iterator[dict[str, Any]]:
    """Rows newer than the watermark, up to the safety bound.

    The bound is evaluated by the database so the comparison uses one clock.
    A client-side bound would be subject to skew between the extractor host
    and the database host.
    """
    sql = f"""
        SELECT *
        FROM {table.name}
        WHERE created_at > %(watermark)s
          AND created_at < (now() - interval '{SAFETY_LAG}')
        ORDER BY created_at, {table.key_column}
    """

    with conn.cursor(name=f"extract_{table.name}", row_factory=dict_row) as cur:
        cur.itersize = CURSOR_ITERSIZE
        cur.execute(sql, {"watermark": watermark})
        yield from cur


def extract_table(
    table: SourceTable,
    writer: LandingWriter,
    store: StateStore,
    *,
    runlog: RunLog,
    dt: date | None = None,
    full_refresh: bool = False,
) -> tuple[Batch, list[LandedPart]] | None:
    """Extract one table incrementally. Returns None if there was nothing new.

    The checkpoint is written inside the context manager's clean exit, so an
    exception anywhere in the landing path leaves the watermark where it was
    and the next run re-reads the same window. See the checkpoint store.

    The run-log attempt wraps the checkpoint, so its row is written after the
    checkpoint commits: success means landed AND checkpointed. Nothing new is
    a no_new_rows row, never an absent one.
    """
    batch = Batch.new(table.source_id, dt=dt)
    bound = log.bind(table=table.name, batch_id=batch.batch_id)

    with runlog.checkpointed_attempt(
        store, table.source_id, full_refresh=full_refresh
    ) as (attempt, checkpoint):
        attempt.bind_batch(batch)
        watermark = EPOCH if full_refresh else (checkpoint.value or EPOCH)
        attempt.watermark_from = watermark
        attempt.details["safety_lag"] = SAFETY_LAG
        bound.info("postgres.extract_start", watermark=watermark, full=full_refresh)

        parts: list[LandedPart] = []
        buffer: list[dict[str, Any]] = []
        high_water: datetime | None = None

        with psycopg.connect(_dsn()) as conn:
            for row in _iter_rows(conn, table, watermark):
                # Dropped at S21 under ADR-019; left in tonight on purpose.
                row["_source_table"] = table.name
                created_at = row["created_at"]
                if high_water is None or created_at > high_water:
                    high_water = created_at

                buffer.append(row)
                # Counted where rows arrive. rows_written comes from the
                # landed parts, so the two can disagree if rows go missing.
                attempt.rows_read += 1

                if len(buffer) >= PART_SIZE:
                    parts.append(
                        writer.write_part(buffer, batch=batch, part_number=len(parts))
                    )
                    buffer = []

        if buffer:
            parts.append(writer.write_part(buffer, batch=batch, part_number=len(parts)))

        if not parts:
            # Nothing new. The watermark deliberately does not advance: there
            # is no evidence about the window between it and the bound, and
            # moving it would discard that window permanently.
            bound.info("postgres.no_new_rows", watermark=watermark)
            attempt.mark_no_new_rows()
            return None

        manifest_key = writer.write_manifest(
            batch=batch,
            parts=parts,
            source_query={
                "table": table.name,
                "watermark_column": "created_at",
                "watermark_from": watermark,
                "watermark_to": high_water.isoformat() if high_water else None,
                "safety_lag": SAFETY_LAG,
                "full_refresh": full_refresh,
                "rows": attempt.rows_read,
                "parts": len(parts),
            },
        )
        attempt.record_landed(parts, manifest_key=manifest_key)

        assert high_water is not None
        checkpoint.advance(
            high_water.isoformat(),
            batch_id=batch.batch_id,
            allow_regression=full_refresh,
        )
        attempt.watermark_to = high_water.isoformat()

        bound.info(
            "postgres.extracted",
            rows=attempt.rows_read,
            parts=len(parts),
            watermark_to=high_water.isoformat(),
        )
        return batch, parts


def extract_all(
    writer: LandingWriter,
    store: StateStore,
    *,
    runlog: RunLog,
    dt: date | None = None,
    full_refresh: bool = False,
) -> dict[str, int]:
    """Extract every configured table. Returns rows landed per table."""
    results: dict[str, int] = {}
    for table in TABLES:
        outcome = extract_table(
            table, writer, store, runlog=runlog, dt=dt, full_refresh=full_refresh
        )
        results[table.name] = sum(p.row_count for p in outcome[1]) if outcome else 0
    return results
