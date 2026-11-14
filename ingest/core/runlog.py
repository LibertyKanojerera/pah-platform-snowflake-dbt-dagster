"""Ingestion run log: one row per extraction attempt.

A manifest records what landed, and exists only when a batch commits. This
log records what was attempted, so it is the only place a failure, a no-op or
a duration can appear. Deriving the log from manifests would be blind to every
run that landed nothing.

Ordering is the one thing to get right. The attempt opens OUTSIDE the
checkpoint, so the checkpoint commits first and the log row is written after
it: status 'success' means landed AND checkpointed, and a checkpoint write
that fails is recorded as a failed attempt. checkpointed_attempt() is the only
place that nesting is written down, so it is tested once rather than trusted
in every source.

Every attempt leaves a row. A clean exit that cannot be recorded truthfully
(no manifest and no no-op mark) is written as failed with RunLogError, then
raised.

Failure handling is asymmetric on purpose:

* Clean exit: a failed log write raises. An observability table that goes
  quietly empty is the ADR-010 failure mode.
* Exception: the original exception wins. If recording the failure also
  fails, that goes to stderr and the original is re-raised, so the traceback
  names the real problem.
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import duckdb
import structlog

from ingest.core.landing import Batch, LandedPart
from ingest.core.log import get_logger
from ingest.core.state import DEFAULT_DUCKDB_PATH, StateStore

log = get_logger(__name__)

SUCCESS = "success"
NO_NEW_ROWS = "no_new_rows"
FAILED = "failed"

#: Exception text is stored, so it is capped. It must never carry a secret:
#: true today, because no source puts a credential in a URL or a message.
MAX_ERROR_CHARS = 2_000

# details is canonical JSON in VARCHAR here and VARIANT in Snowflake. The
# CHECK is DuckDB-only: Snowflake standard tables enforce NOT NULL and nothing
# else, so the status vocabulary is really enforced by the constants above.
DDL = """
CREATE TABLE IF NOT EXISTS ingestion_log (
    attempt_id      VARCHAR PRIMARY KEY,
    run_id          VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    status          VARCHAR NOT NULL
                    CHECK (status IN ('success', 'no_new_rows', 'failed')),
    batch_id        VARCHAR,
    manifest_key    VARCHAR,
    rows_read       BIGINT NOT NULL,
    rows_written    BIGINT NOT NULL,
    parts_written   INTEGER NOT NULL,
    bytes_written   BIGINT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     BIGINT NOT NULL,
    watermark_from  VARCHAR,
    watermark_to    VARCHAR,
    triggered_by    VARCHAR NOT NULL,
    full_refresh    BOOLEAN NOT NULL,
    error_class     VARCHAR,
    error_message   VARCHAR,
    details         VARCHAR NOT NULL
)
"""

COLUMNS = (
    "attempt_id",
    "run_id",
    "source",
    "status",
    "batch_id",
    "manifest_key",
    "rows_read",
    "rows_written",
    "parts_written",
    "bytes_written",
    "started_at",
    "finished_at",
    "duration_ms",
    "watermark_from",
    "watermark_to",
    "triggered_by",
    "full_refresh",
    "error_class",
    "error_message",
    "details",
)


class RunLogError(RuntimeError):
    """An attempt closed in a state that cannot be recorded truthfully."""


@dataclass
class Attempt:
    """One extraction attempt. The source fills it in as it works."""

    run_id: str
    source: str
    triggered_by: str
    full_refresh: bool
    attempt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str | None = None
    batch_id: str | None = None
    manifest_key: str | None = None
    rows_read: int = 0
    rows_written: int = 0
    parts_written: int = 0
    bytes_written: int = 0
    watermark_from: str | None = None
    watermark_to: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def bind_batch(self, batch: Batch) -> None:
        """The prefix this attempt writes to. A manifest exists under it only
        when status is 'success'; on failure, it is where orphaned parts are.
        """
        self.batch_id = batch.batch_id
        structlog.contextvars.bind_contextvars(batch_id=batch.batch_id)

    def record_landed(self, parts: Sequence[LandedPart], *, manifest_key: str) -> None:
        """Written counts come from the parts. rows_read is counted by the
        source where rows arrive; deriving both from one variable would make
        rows_read == rows_written a check that cannot fail.
        """
        self.parts_written = len(parts)
        self.rows_written = sum(p.row_count for p in parts)
        self.bytes_written = sum(p.byte_count for p in parts)
        self.manifest_key = manifest_key

    def mark_no_new_rows(self) -> None:
        self.status = NO_NEW_ROWS


class RunLog(ABC):
    """DuckDB now; Phase 4 adds a Snowflake sibling writing
    PAH_RAW.META.INGESTION_LOG through the same interface."""

    def __init__(self, *, run_id: str, triggered_by: str) -> None:
        self.run_id = run_id
        self.triggered_by = triggered_by

    @abstractmethod
    def _insert(self, row: dict[str, Any]) -> None: ...

    @contextmanager
    def attempt(self, source: str, *, full_refresh: bool = False) -> Iterator[Attempt]:
        attempt = Attempt(
            run_id=self.run_id,
            source=source,
            triggered_by=self.triggered_by,
            full_refresh=full_refresh,
        )
        started = time.monotonic()
        with structlog.contextvars.bound_contextvars(
            source=source, attempt_id=attempt.attempt_id, batch_id=None
        ):
            try:
                yield attempt
                # Inside the try on purpose: an attempt that cannot be recorded
                # truthfully still writes a failed row before raising.
                _finalise_status(attempt)
            except BaseException as exc:
                # BaseException, so a Ctrl-C halfway through a backfill leaves
                # a row rather than an attempt that never happened.
                attempt.status = FAILED
                try:
                    self._insert(_row(attempt, started, exc))
                except Exception as log_exc:
                    log.error("runlog.write_failed", error=repr(log_exc))
                raise
            # Outside the try on purpose: a failed write on a clean exit must
            # fail the run, not be recorded as a failure it then cannot write.
            self._insert(_row(attempt, started, None))
            log.info(
                "runlog.recorded", status=attempt.status, rows=attempt.rows_written
            )

    @contextmanager
    def checkpointed_attempt(
        self, store: StateStore, source: str, *, full_refresh: bool = False
    ) -> Iterator[tuple[Attempt, Any]]:
        """Attempt outside, checkpoint inside: the only correct order.
        Narrow Any to the type store.checkpoint() yields."""
        with self.attempt(source, full_refresh=full_refresh) as attempt:
            with store.checkpoint(source) as checkpoint:
                yield attempt, checkpoint


def _finalise_status(attempt: Attempt) -> None:
    if attempt.status is None:
        attempt.status = SUCCESS
    if attempt.status == SUCCESS and attempt.manifest_key is None:
        raise RunLogError(
            f"{attempt.source}: clean exit with no manifest; call "
            "record_landed() or mark_no_new_rows()"
        )
    if attempt.status == NO_NEW_ROWS and attempt.rows_written:
        raise RunLogError(f"{attempt.source}: no_new_rows but rows were written")


def _row(attempt: Attempt, started: float, exc: BaseException | None) -> dict[str, Any]:
    row = asdict(attempt)
    row["details"] = json.dumps(row["details"], sort_keys=True, default=str)
    row["finished_at"] = datetime.now(timezone.utc)
    # Monotonic: an NTP step during a long backfill cannot produce a negative
    # or inflated duration. Wall-clock is for ordering; this is for measuring.
    row["duration_ms"] = int((time.monotonic() - started) * 1000)
    row["error_class"] = type(exc).__name__ if exc else None
    row["error_message"] = str(exc)[:MAX_ERROR_CHARS] if exc else None
    return row


class DuckDBRunLog(RunLog):
    """Same file as the checkpoint store, opened per write for the same
    single-writer reason."""

    def __init__(
        self, *, run_id: str, triggered_by: str, path: str = DEFAULT_DUCKDB_PATH
    ) -> None:
        super().__init__(run_id=run_id, triggered_by=triggered_by)
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(path) as con:
            con.execute(DDL)

    def _insert(self, row: dict[str, Any]) -> None:
        sql = (
            f"INSERT INTO ingestion_log ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in COLUMNS)})"
        )
        with duckdb.connect(self._path) as con:
            con.execute(sql, [row[c] for c in COLUMNS])
