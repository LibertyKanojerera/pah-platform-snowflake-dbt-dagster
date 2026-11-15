"""Provenance contract (ADR-019), checked against what is in the bucket.

The first run of this file is the S20 audit: each failure is a finding. It is
written to the target contract, so it is red until the G2 clean run re-lands
everything. Green at G2 means every landed row satisfies the contract, not
that the code was read and looked right.
"""

from __future__ import annotations

from typing import Iterator

import duckdb
import pytest

from ingest.core.config import get_settings
from ingest.core.duck import s3_connection
from ingest.core.landing import PROVENANCE_COLUMNS

pytestmark = pytest.mark.integration

SOURCES = (
    "cms_part_d",
    "nppes",
    "app_postgres_patients",
    "app_postgres_patient_events",
)

#: ADR-019 naming rule: source metadata carries its source's prefix.
METADATA_PREFIX = {
    "cms_part_d": "_cms_",
    "nppes": "_nppes_",
    "app_postgres_patients": "_pg_",
    "app_postgres_patient_events": "_pg_",
}


def _parquet(source: str) -> str:
    # Production root on purpose, not the landing_root setting: once finding 1
    # lands, the integration conftest points that setting at landing_test/.
    glob = f"s3://{get_settings().landing_bucket}/landing/{source}/*/*/*.parquet"
    return (
        f"read_parquet('{glob}', hive_partitioning = true, "
        "filename = true, union_by_name = true)"
    )


@pytest.fixture(scope="module")
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    with s3_connection() as connection:
        yield connection


def _columns(con: duckdb.DuckDBPyConnection, source: str) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {_parquet(source)}").fetchall()
    return {name: dtype for name, dtype, *_ in rows}


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("source", SOURCES)
def test_every_provenance_column_is_present(
    con: duckdb.DuckDBPyConnection, source: str
) -> None:
    missing = set(PROVENANCE_COLUMNS) - set(_columns(con, source))
    assert not missing, missing


def test_provenance_types_agree_across_sources(con: duckdb.DuckDBPyConnection) -> None:
    types = {
        s: {c: t for c, t in _columns(con, s).items() if c in PROVENANCE_COLUMNS}
        for s in SOURCES
    }
    assert len({tuple(sorted(t.items())) for t in types.values()}) == 1, types


@pytest.mark.parametrize("source", SOURCES)
def test_row_level_contract(con: duckdb.DuckDBPyConnection, source: str) -> None:
    row = con.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE _source IS DISTINCT FROM '{source}'),
            count(*) FILTER (WHERE _batch_id IS DISTINCT FROM batch::VARCHAR),
            count(*) FILTER (WHERE _file_name IS DISTINCT FROM
                             regexp_replace(filename, '^s3://[^/]+/', '')),
            count(*) FILTER (WHERE _ingested_at IS NULL),
            count(*) FILTER (WHERE _source_row_hash IS NULL OR NOT
                             regexp_full_match(_source_row_hash, '[0-9a-f]{{64}}'))
        FROM {_parquet(source)}
        """
    ).fetchone()
    assert row is not None
    names = (
        "source_not_path",
        "batch_id_not_path",
        "file_name_not_key",
        "ingested_at_null",
        "hash_malformed",
    )
    violations = {n: v for n, v in zip(names, row) if v}
    assert not violations, violations


@pytest.mark.parametrize("source", SOURCES)
def test_extracted_at_never_follows_ingested_at(
    con: duckdb.DuckDBPyConnection, source: str
) -> None:
    bad = _count(
        con,
        f"SELECT count(*) FROM {_parquet(source)} "
        "WHERE _extracted_at IS NULL OR _extracted_at > _ingested_at",
    )
    assert bad == 0, bad


@pytest.mark.parametrize("source", SOURCES)
def test_batch_values_are_constant_and_hashes_unique(
    con: duckdb.DuckDBPyConnection, source: str
) -> None:
    bad = con.execute(
        f"""
        SELECT _batch_id FROM {_parquet(source)}
        GROUP BY _batch_id
        HAVING count(DISTINCT _ingested_at) > 1
            OR count(DISTINCT _source) > 1
            OR count(*) > count(DISTINCT _source_row_hash)
        """
    ).fetchall()
    assert not bad, bad[:5]


@pytest.mark.parametrize("source", SOURCES)
def test_underscore_columns_are_provenance_or_source_prefixed(
    con: duckdb.DuckDBPyConnection, source: str
) -> None:
    stray = {
        c
        for c in _columns(con, source)
        if c.startswith("_")
        and c not in PROVENANCE_COLUMNS
        and not c.startswith(METADATA_PREFIX[source])
    }
    assert not stray, stray
