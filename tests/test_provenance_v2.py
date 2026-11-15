"""ADR-019, finding 4: _extracted_at records when a value was read from its
source. It equals the batch time for anything read live, and is earlier only
when the value came from a cache. It is provenance, so it is not hashed.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

from ingest.core.cache import JsonCache
from ingest.core.landing import Batch, add_provenance, row_hash
from ingest.sources.nppes import flatten, iter_lookups
from tests.conftest import FakeHttpClient

RECORD = {"basic": {}, "taxonomies": [], "addresses": []}


def test_extracted_at_defaults_to_the_batch_time() -> None:
    batch = Batch.new("cms_part_d")
    (row,) = add_provenance(
        [{"npi": "1"}], batch=batch, object_key=batch.key("p.parquet")
    )
    assert row["_extracted_at"] == batch.ingested_at


def test_a_supplied_extracted_at_is_kept_and_not_hashed() -> None:
    batch = Batch.new("nppes")
    earlier = batch.ingested_at - timedelta(days=10)
    (row,) = add_provenance(
        [{"npi": "1", "_extracted_at": earlier}],
        batch=batch,
        object_key=batch.key("p.parquet"),
    )
    assert row["_extracted_at"] == earlier
    assert row_hash({"npi": "1", "_extracted_at": earlier}) == row_hash({"npi": "1"})


def test_a_cache_hit_carries_its_fetch_time_and_a_live_read_does_not(
    tmp_path: Path,
) -> None:
    cache = JsonCache(path=str(tmp_path / "c.duckdb"), namespace="test")
    client = FakeHttpClient(
        lambda path, params: {"result_count": 1, "results": [RECORD]}
    )

    (live,) = list(iter_lookups(client, ["1"], rate=1000, cache=cache))
    (hit,) = list(iter_lookups(client, ["1"], rate=1000, cache=cache))

    assert len(client.calls) == 1, "the second lookup should have come from cache"
    assert live.fetched_at is None
    assert "_extracted_at" not in flatten(live)

    assert hit.fetched_at is not None
    extracted = flatten(hit)["_extracted_at"]
    assert extracted == hit.fetched_at
    assert extracted.utcoffset() == timedelta(0), "must be UTC, like _ingested_at"
    assert extracted.tzinfo == timezone.utc


def test_a_cached_miss_also_carries_its_fetch_time(tmp_path: Path) -> None:
    """A miss is an observation too: 'not registered, as of fetched_at'."""
    cache = JsonCache(path=str(tmp_path / "c.duckdb"), namespace="test")
    client = FakeHttpClient(lambda path, params: {"result_count": 0, "results": []})

    list(iter_lookups(client, ["1"], rate=1000, cache=cache))
    (hit,) = list(iter_lookups(client, ["1"], rate=1000, cache=cache))

    assert hit.fetched_at is not None
    assert "_extracted_at" in flatten(hit)
