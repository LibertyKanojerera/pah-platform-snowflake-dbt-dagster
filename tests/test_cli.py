# tests/test_cli.py
from ingest.cli import build_parser
from ingest.sources.cms_part_d import CmsDataset, releases_by_year


def test_parser_accepts_each_subcommand() -> None:
    parser = build_parser()
    for command in ("cms", "nppes", "postgres", "all"):
        assert parser.parse_args([command]).command == command


def test_parser_rejects_an_unknown_subcommand() -> None:
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["nonsense"])


def _ds(uuid: str, end: str) -> CmsDataset:
    return CmsDataset(
        title=f"Part D Prescribers - by Provider and Drug : {end}",
        uuid=uuid,
        data_url=f"https://x/dataset/{uuid}/data",
        modified=None,
        temporal=[
            {"@type": "PeriodOfTime", "startDate": end[:4] + "-01-01", "endDate": end}
        ],
    )


def test_duplicate_release_registrations_collapse_to_one() -> None:
    """2024 is registered twice under distinct UUIDs holding identical data.
    Verified by row count at plan S12; enforced here.
    """
    years = releases_by_year(
        [
            _ds("aaa", "2024-12-31"),
            _ds("bbb", "2024-12-31"),
            _ds("ccc", "2023-12-31"),
        ]
    )
    assert list(years) == ["2023", "2024"]
    assert years["2024"].uuid == "aaa"
