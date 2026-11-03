from __future__ import annotations

from pathlib import Path

import pytest

from ingest.sources.cms_part_d import (
    AmbiguousDataset,
    CmsDataset,
    DrugVocabulary,
    PaginationIncomplete,
    UnknownDrugValues,
    count_rows,
    iter_rows,
    resolve_dataset,
    sweep_for_unknown_drugs,
)
from tests.conftest import FakeHttpClient

DATA_URL = "https://example.test/data-api/v1/dataset/aaa/data"


def _dataset(uuid: str = "aaa", temporal: object = None) -> CmsDataset:
    return CmsDataset(
        title="Part D Prescribers - by Provider and Drug : 2023-12-31",
        uuid=uuid,
        data_url=DATA_URL.replace("aaa", uuid),
        modified="2024-06-04",
        temporal=temporal
        or [
            {
                "@type": "PeriodOfTime",
                "startDate": "2023-01-01",
                "endDate": "2023-12-31",
            }
        ],
    )


def _pager(total: int, rows: list[dict[str, str]]):
    """Serves /stats then pages of `rows` sliced by offset."""

    def handler(path: str, params: dict) -> object:
        if path.endswith("/stats"):
            return {"found_rows": total, "total_rows": 99999}
        offset = int(params.get("offset", 0))
        size = int(params.get("size", 10))
        return rows[offset : offset + size]

    return handler


def test_count_rows_uses_found_rows_not_total_rows() -> None:
    """found_rows honours the filter; total_rows is the whole table.

    Paginating against total_rows would issue thousands of requests to yield
    a few hundred rows.
    """
    client = FakeHttpClient(_pager(450, []))
    assert count_rows(client, _dataset()) == 450


def test_iter_rows_yields_every_row_across_pages() -> None:
    rows = [{"i": str(i)} for i in range(25)]
    client = FakeHttpClient(_pager(25, rows))
    assert len(list(iter_rows(client, _dataset(), page_size=10))) == 25


def test_iter_rows_raises_when_the_api_truncates() -> None:
    """An empty page before the promised total is silent truncation.

    Stopping quietly here would land a partial batch carrying a valid
    manifest, which downstream cannot distinguish from a complete one.
    """
    rows = [{"i": str(i)} for i in range(5)]
    client = FakeHttpClient(_pager(10, rows))
    with pytest.raises(PaginationIncomplete):
        list(iter_rows(client, _dataset(), page_size=10))


def test_iter_rows_raises_when_the_api_over_delivers() -> None:
    rows = [{"i": str(i)} for i in range(10)]
    client = FakeHttpClient(_pager(5, rows))
    with pytest.raises(PaginationIncomplete):
        list(iter_rows(client, _dataset(), page_size=10))


def test_resolve_dataset_collapses_identical_duplicates() -> None:
    """2024-12-01 is registered twice under distinct UUIDs holding the same
    data. Tolerated, but never silently."""
    datasets = [_dataset("aaa"), _dataset("bbb")]
    assert resolve_dataset(datasets, title_contains="2023-12-31").uuid == "aaa"


def test_resolve_dataset_refuses_differing_temporal() -> None:
    other = [
        {"@type": "PeriodOfTime", "startDate": "2022-01-01", "endDate": "2022-12-31"}
    ]
    datasets = [_dataset("aaa"), _dataset("bbb", temporal=other)]
    with pytest.raises(AmbiguousDataset):
        resolve_dataset(datasets, title_contains="2023-12-31")


def test_vocabulary_loads_from_the_dbt_seed() -> None:
    """The seed is the single source of truth, shared with dbt."""
    vocab = DrugVocabulary.from_csv(Path("transform/seeds/drug_vocabulary.csv"))
    assert "Treprostinil Diolamine" in vocab.generic_names
    assert ("Treprostinil/Neb Accessories", "Tyvaso Refill Kit") in vocab.known_pairs


def test_sweep_raises_on_a_value_absent_from_the_vocabulary() -> None:
    """Exact-match filtering can only return what the vocabulary knows, so a
    new formulation would be omitted and look like a quiet quarter."""
    vocab = DrugVocabulary.from_csv(Path("transform/seeds/drug_vocabulary.csv"))

    def handler(path: str, params: dict) -> object:
        return [{"Gnrc_Name": "Treprostinil Novel Salt", "Brnd_Name": "Newdrug"}]

    with pytest.raises(UnknownDrugValues, match="Newdrug"):
        sweep_for_unknown_drugs(FakeHttpClient(handler), _dataset(), vocab)


def test_sweep_passes_when_every_value_is_known() -> None:
    vocab = DrugVocabulary.from_csv(Path("transform/seeds/drug_vocabulary.csv"))

    def handler(path: str, params: dict) -> object:
        return [{"Gnrc_Name": "Selexipag", "Brnd_Name": "Uptravi"}]

    sweep_for_unknown_drugs(FakeHttpClient(handler), _dataset(), vocab)
