"""Keyed JSON cache backed by DuckDB.

Separate file from the checkpoint store on purpose. The cache is disposable —
delete it and the next run re-fetches — while the checkpoint is not. Sharing a
file would mean you cannot throw away the cache without risking state that
must survive.

Negative results are cached deliberately: "this NPI does not exist" is a fact
worth remembering, and without it every run re-queries the same misses. Errors
are never cached, so a transient failure retries on the next run rather than
becoming a hole that persists for the TTL.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb

from ingest.core.log import get_logger

log = get_logger(__name__)

DEFAULT_CACHE_PATH = "data/cache.duckdb"
DEFAULT_TTL_DAYS = 30


@dataclass(frozen=True)
class CacheEntry:
    key: str
    found: bool
    payload: dict[str, Any] | None
    fetched_at: datetime


class JsonCache:
    def __init__(
        self,
        *,
        path: str = DEFAULT_CACHE_PATH,
        namespace: str,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self._path = path
        self.namespace = namespace
        self.ttl = timedelta(days=ttl_days)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self.hits = 0
        self.misses = 0

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._path)
        try:
            yield con
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace   VARCHAR     NOT NULL,
                    key         VARCHAR     NOT NULL,
                    found       BOOLEAN     NOT NULL,
                    payload     VARCHAR,
                    fetched_at  TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )

    def get(self, key: str) -> CacheEntry | None:
        """Return a live entry, or None if absent or expired."""
        with self._connect() as con:
            row = con.execute(
                "SELECT key, found, payload, fetched_at FROM cache_entries "
                "WHERE namespace = ? AND key = ?",
                [self.namespace, key],
            ).fetchone()

        if row is None:
            self.misses += 1
            return None

        fetched_at: datetime = row[3]
        if datetime.now(timezone.utc) - fetched_at > self.ttl:
            self.misses += 1
            return None

        self.hits += 1
        return CacheEntry(
            key=row[0],
            found=row[1],
            payload=json.loads(row[2]) if row[2] else None,
            fetched_at=fetched_at,
        )

    def put(self, key: str, *, found: bool, payload: dict[str, Any] | None) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO cache_entries (namespace, key, found, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (namespace, key) DO UPDATE SET
                    found = excluded.found,
                    payload = excluded.payload,
                    fetched_at = excluded.fetched_at
                """,
                [
                    self.namespace,
                    key,
                    found,
                    (
                        json.dumps(payload, sort_keys=True, separators=(",", ":"))
                        if payload is not None
                        else None
                    ),
                    datetime.now(timezone.utc),
                ],
            )

    def purge(self) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT count(*) FROM cache_entries WHERE namespace = ?",
                [self.namespace],
            ).fetchone()
            before = row[0] if row is not None else 0
            con.execute(
                "DELETE FROM cache_entries WHERE namespace = ?", [self.namespace]
            )
        log.info("cache.purged", namespace=self.namespace, removed=before)
        return int(before)
