from __future__ import annotations

import json

import pytest

from ingest.core.landing import (
    PROVENANCE_COLUMNS,
    Batch,
    LandingWriter,
    add_provenance,
    row_hash,
)


def test_row_hash_is_independent_of_key_order() -> None:
    a = {"npi": "123", "drug": "Orenitram Er", "claims": 42}
    b = {"claims": 42, "drug": "Orenitram Er", "npi": "123"}
    assert row_hash(a) == row_hash(b)


def test_row_hash_ignores_provenance_columns() -> None:
    """A row's identity is its source content, not the run that carried it.

    If provenance were hashed, every re-run would produce new hashes and the
    idempotency assertion would be vacuously false.
    """
    source = {"npi": "123", "drug": "Orenitram Er"}
    batch = Batch.new("cms_part_d")
    enriched = add_provenance([source], batch=batch, file_name="part-0000.parquet")[0]
    assert row_hash(enriched) == row_hash(source)


def test_row_hash_changes_with_content() -> None:
    assert row_hash({"npi": "123"}) != row_hash({"npi": "124"})


def test_add_provenance_sets_every_column() -> None:
    batch = Batch.new("cms_part_d")
    row = add_provenance([{"npi": "123"}], batch=batch, file_name="p.parquet")[0]
    for column in PROVENANCE_COLUMNS:
        assert column in row, column
    assert row["_batch_id"] == batch.batch_id


def test_batch_prefix_is_hive_partitioned() -> None:
    batch = Batch.new("cms_part_d")
    assert batch.prefix.startswith("landing/cms_part_d/dt=")
    assert f"/batch={batch.batch_id}" in batch.prefix


def test_two_batches_do_not_collide() -> None:
    """A re-run must write a new prefix, never overwrite the previous one."""
    assert Batch.new("cms_part_d").prefix != Batch.new("cms_part_d").prefix


def test_write_part_refuses_empty_input(fake_s3) -> None:
    writer = LandingWriter(bucket="b", s3_client=fake_s3)
    with pytest.raises(ValueError):
        writer.write_part([], batch=Batch.new("cms_part_d"))


def test_manifest_is_written_after_the_data(fake_s3) -> None:
    """The manifest is a commit marker: a prefix carrying one is complete.

    Ordering matters, so assert the data key exists before the manifest is
    written rather than only that both exist at the end.
    """
    writer = LandingWriter(bucket="b", s3_client=fake_s3)
    batch = Batch.new("cms_part_d")

    part = writer.write_part([{"npi": "123"}], batch=batch)
    assert f"b/{part.key}" in fake_s3.objects
    assert not any(k.endswith("_manifest.json") for k in fake_s3.objects)

    writer.write_manifest(batch=batch, parts=[part], source_query={"q": 1})
    manifest_key = f"b/{batch.key('_manifest.json')}"
    assert manifest_key in fake_s3.objects

    manifest = json.loads(fake_s3.objects[manifest_key])
    assert manifest["row_count_total"] == 1
    assert manifest["parts"][0]["key"] == part.key


def test_both_runs_produce_identical_source_row_hashes(landed) -> None:
    """The core idempotency assertion.

    Equal counts alone would pass even if the content differed. Hashing both
    row sets with the same function landing uses proves the extraction is
    deterministic.

    Hashes are recomputed here rather than read off the rows: provenance is
    attached inside write_part, which returns a new list, so the rows
    land_release hands back are pre-provenance by design. That the landed
    hash excludes provenance is covered by the unit suite.
    """
    first, second = landed
    assert {row_hash(r) for r in first} == {row_hash(r) for r in second}
