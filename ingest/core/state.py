"""Ingestion checkpoints.

A checkpoint records the high-water mark a source has been successfully
extracted up to. It is written only after the batch it describes has landed,
and the mechanism for that is structural rather than disciplined: the write
lives after the `yield` in a context manager, so an exception in the body
propagates and the write is never reached. There is no rollback to forget.

This is the corrected form of a bug carried over from the Databricks build,
where Auto Loader's checkpoint advanced independently of whether downstream
work succeeded. A failure after the checkpoint moved left data marked as
consumed that had never arrived, and nothing errored afterwards: the next run
simply started past the lost window. Silent data loss, green pipeline.

The watermark is a string and its meaning belongs to the source:
  cms_part_d    dataset version or release year
  app_postgres  ISO-8601 event timestamp
  nppes         unused; enrichment is not incremental

String comparison is lexicographic, which orders ISO timestamps and
zero-padded versions correctly. A source whose watermark does not sort that
way must not rely on the regression guard.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import duckdb

from ingest.core.log import get_logger

log = get_logger(__name__)

DEFAULT_DUCKDB_PATH = "data/pah_local.duckdb"


class CheckpointRegression(Exception):
    """Refused to move a watermark backwards."""


@dataclass(frozen=True)
class Checkpoint:
    source: str
    watermark: str | None
    batch_id: str | None
    updated_at: datetime
    run_count: int


class PendingCheckpoint:
    """Handed to the caller inside `StateStore.checkpoint()`.

    `advance()` only stages a value. Nothing is persisted until the context
    manager exits cleanly.
    """

    def __init__(self, *, source: str, current: Checkpoint | None) -> None:
        self.source = source
        self._current = current
        self._staged: tuple[str, str] | None = None
        self._allow_regression = False

    @property
    def value(self) -> str | None:
        """The last successfully committed watermark, or None on first run."""
        return self._current.watermark if self._current else None

    @property
    def run_count(self) -> int:
        return self._current.run_count if self._current else 0

    def advance(
        self, watermark: str, *, batch_id: str, allow_regression: bool = False
    ) -> None:
        self._staged = (watermark, batch_id)
        self._allow_regression = allow_regression


class StateStore(ABC):
    @abstractmethod
    def read(self, source: str) -> Checkpoint | None: ...

    @abstractmethod
    def _persist(self, checkpoint: Checkpoint) -> None: ...

    @contextmanager
    def checkpoint(self, source: str) -> Iterator[PendingCheckpoint]:
        current = self.read(source)
        pending = PendingCheckpoint(source=source, current=current)

        # No try/except by design. If the body raises, control never returns
        # here and nothing is written. The failure branch has no write path.
        yield pending

        if pending._staged is None:
            log.info("state.not_advanced", source=source, watermark=pending.value)
            return

        watermark, batch_id = pending._staged

        if (
            current is not None
            and current.watermark is not None
            and watermark < current.watermark
            and not pending._allow_regression
        ):
            raise CheckpointRegression(
                f"{source}: refusing {watermark!r} behind {current.watermark!r}; "
                "pass allow_regression=True for a deliberate full refresh"
            )

        updated = Checkpoint(
            source=source,
            watermark=watermark,
            batch_id=batch_id,
            updated_at=datetime.now(timezone.utc),
            run_count=(current.run_count if current else 0) + 1,
        )
        self._persist(updated)

        log.info(
            "state.advanced",
            source=source,
            previous=current.watermark if current else None,
            watermark=watermark,
            batch_id=batch_id,
            run_count=updated.run_count,
        )


class DuckDBStateStore(StateStore):
    """Local checkpoint store. Phase 4 adds a Snowflake-backed sibling
    writing to META.INGESTION_STATE with the same interface."""

    def __init__(self, path: str = DEFAULT_DUCKDB_PATH) -> None:
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        # Opened per operation: DuckDB allows one writer, and a long-held
        # connection would block the CLI and Dagster running side by side.
        con = duckdb.connect(self._path)
        try:
            yield con
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_state (
                    source      VARCHAR PRIMARY KEY,
                    watermark   VARCHAR,
                    batch_id    VARCHAR,
                    updated_at  TIMESTAMPTZ NOT NULL,
                    run_count   BIGINT NOT NULL
                )
                """
            )
            # Append-only, so a watermark that moved wrongly can be traced
            # rather than inferred.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_state_history (
                    source      VARCHAR NOT NULL,
                    watermark   VARCHAR,
                    batch_id    VARCHAR,
                    updated_at  TIMESTAMPTZ NOT NULL,
                    run_count   BIGINT NOT NULL
                )
                """
            )

    def read(self, source: str) -> Checkpoint | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT source, watermark, batch_id, updated_at, run_count "
                "FROM ingestion_state WHERE source = ?",
                [source],
            ).fetchone()

        if row is None:
            return None

        return Checkpoint(
            source=row[0],
            watermark=row[1],
            batch_id=row[2],
            updated_at=row[3],
            run_count=row[4],
        )

    def _persist(self, checkpoint: Checkpoint) -> None:
        values = [
            checkpoint.source,
            checkpoint.watermark,
            checkpoint.batch_id,
            checkpoint.updated_at,
            checkpoint.run_count,
        ]
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO ingestion_state
                    (source, watermark, batch_id, updated_at, run_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (source) DO UPDATE SET
                    watermark  = excluded.watermark,
                    batch_id   = excluded.batch_id,
                    updated_at = excluded.updated_at,
                    run_count  = excluded.run_count
                """,
                values,
            )
            con.execute(
                "INSERT INTO ingestion_state_history "
                "(source, watermark, batch_id, updated_at, run_count) "
                "VALUES (?, ?, ?, ?, ?)",
                values,
            )
