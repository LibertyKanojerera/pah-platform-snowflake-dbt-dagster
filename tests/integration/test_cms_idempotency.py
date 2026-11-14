"""Idempotency against the live CMS API and real S3.

Excluded from default runs and from CI: slow, network-dependent, and a fork
pull request has no AWS credentials. Run with `pytest -m integration`.

What is being asserted is not "the second run writes the same objects" — it
writes a new batch prefix by design. It is that the same source state yields
the same set of source-row hashes, and that the natural key is unique within
a batch, so merging the second batch after the first is a no-op.
"""

from __future__ import annotations

import collections
import os

import pytest

from ingest.core.http import HttpClient
from ingest.core.landing import LandingWriter, row_hash
from ingest.sources.cms_part_d import (
    DrugVocabulary,
    discover_datasets,
    land_release,
    resolve_dataset,
)
from tests.conftest import MemoryRunLog

pytestmark = pytest.mark.integration

RELEASE = "2023-12-31"
BUCKET = os.environ.get("LANDING_BUCKET", "")


@pytest.fixture(scope="module")
def landed() -> list[list[dict]]:
    """Extract the same release twice and return both row sets."""
    if not BUCKET:
        pytest.skip("LANDING_BUCKET not set")

    vocab = DrugVocabulary.from_csv()
    writer = LandingWriter(bucket=BUCKET)
    # In memory, so test runs never write to the real ingestion_log.
    runlog = MemoryRunLog()
    runs: list[list[dict]] = []

    with HttpClient(source="cms_part_d", base_url="https://data.cms.gov") as client:
        dataset = resolve_dataset(discover_datasets(client), title_contains=RELEASE)
        for _ in range(2):
            with runlog.attempt("cms_part_d") as attempt:
                _batch, _part, rows = land_release(
                    client, dataset, vocab, writer, attempt=attempt
                )
            runs.append(rows)

    return runs


def test_both_runs_return_the_same_row_count(landed) -> None:
    first, second = landed
    assert len(first) == len(second)


def test_both_runs_produce_identical_source_row_hashes(landed) -> None:
    """The core idempotency assertion.

    Equal counts alone would pass even if the content differed. Hashing both
    row sets with the same function landing uses proves the extraction is
    deterministic.

    Hashes are recomputed here rather than read off the rows: provenance is
    attached inside write_part, which returns a new list, so the rows
    land_release hands back are pre-provenance by design. That the landed hash
    excludes provenance is covered by the unit suite.
    """
    first, second = landed
    assert {row_hash(r) for r in first} == {row_hash(r) for r in second}


def test_natural_key_is_unique_within_a_batch(landed) -> None:
    """Grain was measured on one brand at plan S11. This checks it across all
    of them, which is what fct_claims' unique_key will rely on at plan S32.
    """
    rows = landed[0]
    keys = [(r["Prscrbr_NPI"], r["Brnd_Name"], r["_cms_release_year"]) for r in rows]
    duplicates = {k: n for k, n in collections.Counter(keys).items() if n > 1}
    assert not duplicates, f"duplicate natural keys: {list(duplicates)[:5]}"


def test_generic_name_is_not_needed_in_the_key(landed) -> None:
    """Veletri appears under two generic names across releases. If both ever
    populate in one year, the key needs Gnrc_Name too. Fails loudly if so.
    """
    rows = landed[0]
    by_brand: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for r in rows:
        by_brand[(r["Prscrbr_NPI"], r["Brnd_Name"])].add(r["Gnrc_Name"])

    ambiguous = {k: v for k, v in by_brand.items() if len(v) > 1}
    assert not ambiguous, f"brand under multiple generics: {list(ambiguous)[:5]}"


def test_merging_the_second_batch_is_a_noop(landed) -> None:
    """Union across both batch prefixes, deduplicated on the natural key,
    must equal one batch. This is what the Snowflake MERGE at plan S57 does.
    """
    first, second = landed

    def key(row: dict) -> tuple[str, str, str]:
        return (row["Prscrbr_NPI"], row["Brnd_Name"], row["_cms_release_year"])

    assert len({key(r) for r in first} | {key(r) for r in second}) == len(first)
