"""CMS Medicare Part D Prescribers - by Provider and Drug.

Discovery and pagination only. Vocabulary filtering and landing are plan S12.

Two properties of this API drive the design, both established by the
reconnaissance in docs/sources.md:

* One dataset UUID per release, with no version parameter, so the year-to-UUID
  map is resolved at runtime from the DCAT catalog rather than hardcoded. A new
  release then needs no code change, and the same query feeds the Dagster
  sensor at plan S44.
* A hard page ceiling of 6500 rows and a bare-array response with no count and
  no next link, so the loop derives its own termination from /data/stats and
  verifies the result rather than trusting the last short page.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Mapping

from ingest.core.http import HttpClient
from ingest.core.landing import Batch, LandedPart, LandingWriter
from ingest.core.log import get_logger
from ingest.core.runlog import Attempt, RunLog
from ingest.core.state import StateStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOCABULARY_PATH = _REPO_ROOT / "transform" / "seeds" / "drug_vocabulary.csv"

log = get_logger(__name__)

CMS_BASE_URL = "https://data.cms.gov"
CATALOG_PATH = "/data.json"
DATASET_TITLE_PATTERN = re.compile(
    r"Part D Prescribers - by Provider and Drug", re.IGNORECASE
)

#: Observed ceiling, not a documented one. Requesting more returns 6500.
#: See docs/sources.md.
MAX_PAGE_SIZE = 6500


class AmbiguousDataset(Exception):
    """More than one dataset matched and the module will not guess."""


class PaginationIncomplete(Exception):
    """The crawl ended with a different row count than the API promised."""


@dataclass(frozen=True)
class CmsDataset:
    title: str
    uuid: str
    data_url: str
    modified: str | None
    temporal: str | None

    @property
    def stats_url(self) -> str:
        return f"{self.data_url}/stats"


def _uuid_from_url(url: str) -> str:
    match = re.search(
        r"/dataset/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
        url,
    )
    if not match:
        raise ValueError(f"no dataset uuid in {url!r}")
    return match.group(1)


def discover_datasets(client: HttpClient) -> list[CmsDataset]:
    """Every release of this dataset, newest title first.

    The catalog is the authority. Nothing about the set of releases is
    hardcoded, so a new publication appears without a code change.
    """
    catalog: dict[str, Any] = client.get(CATALOG_PATH).json()

    found: list[CmsDataset] = []
    for entry in catalog.get("dataset", []):
        title = entry.get("title", "")
        if not DATASET_TITLE_PATTERN.search(title):
            continue

        for dist in entry.get("distribution", []) or []:
            access_url = dist.get("accessURL")
            if not access_url or not access_url.endswith("/data"):
                continue

            found.append(
                CmsDataset(
                    title=title,
                    uuid=_uuid_from_url(access_url),
                    data_url=access_url,
                    modified=entry.get("modified"),
                    temporal=entry.get("temporal"),
                )
            )

    found.sort(key=lambda d: d.title, reverse=True)
    log.info("cms.datasets_discovered", count=len(found))
    return found


def resolve_dataset(datasets: list[CmsDataset], *, title_contains: str) -> CmsDataset:
    """Pick exactly one release, or refuse.

    The catalog registers some releases twice under one title, with distinct
    UUIDs holding identical data. 2024-12-01 is registered twice and both
    UUIDs report found_rows 450 for Orenitram Er against total_rows
    28,023,892. Duplicates that agree on `temporal` are therefore collapsed to
    the first; anything that disagrees still raises, because that would mean
    two genuinely different cuts and the module must not guess which is meant.
    """
    matches = [d for d in datasets if title_contains in d.title]

    if not matches:
        raise LookupError(f"no dataset title contains {title_contains!r}")

    distinct_periods = {str(d.temporal) for d in matches}
    if len(distinct_periods) > 1:
        raise AmbiguousDataset(
            f"{len(matches)} datasets match {title_contains!r} with differing "
            "temporal coverage: "
            + ", ".join(f"{d.uuid} ({d.temporal})" for d in matches)
        )

    if len(matches) > 1:
        log.warning(
            "cms.duplicate_registration",
            title_contains=title_contains,
            uuids=[d.uuid for d in matches],
            chosen=matches[0].uuid,
        )

    return matches[0]


def count_rows(
    client: HttpClient,
    dataset: CmsDataset,
    *,
    filters: Mapping[str, str] | None = None,
) -> int:
    """Total rows the API says match. /data/stats honours filters."""
    stats: dict[str, Any] = client.get(
        dataset.stats_url, params=dict(filters or {})
    ).json()
    total = int(stats["found_rows"])
    log.info("cms.row_count", uuid=dataset.uuid, found_rows=total)
    return total


def iter_rows(
    client: HttpClient,
    dataset: CmsDataset,
    *,
    filters: Mapping[str, str] | None = None,
    page_size: int = MAX_PAGE_SIZE,
    expected_total: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every matching row, paging by offset.

    The total is fetched first so the loop knows where it ends. A bare-array
    response gives no other signal, and inferring the end from a short page
    cannot distinguish "that was the last page" from "the API truncated the
    response", which is the silent-truncation failure this guards against.
    """
    stride = min(page_size, MAX_PAGE_SIZE)
    total = (
        expected_total
        if expected_total is not None
        else count_rows(client, dataset, filters=filters)
    )

    bound = log.bind(uuid=dataset.uuid, total=total, stride=stride)
    offset = 0
    yielded = 0

    while offset < total:
        params = dict(filters or {})
        params["size"] = str(stride)
        params["offset"] = str(offset)

        page: list[dict[str, Any]] = client.get(dataset.data_url, params=params).json()

        if not page:
            # The API promised more rows and returned none. Stopping quietly
            # here would land a partial batch that looks complete.
            raise PaginationIncomplete(
                f"empty page at offset {offset} with {total} rows expected"
            )

        for row in page:
            yield row

        yielded += len(page)
        offset += len(page)

        bound.debug("cms.page", offset=offset, rows=len(page), yielded=yielded)

    if yielded != total:
        raise PaginationIncomplete(f"crawled {yielded} rows, API reported {total}")

    bound.info("cms.crawl_complete", yielded=yielded)


#: Stems used by the unknown-value sweep. Deliberately the molecule names
#: rather than the stored values, since the point is to find stored values the
#: vocabulary does not yet know about.
MOLECULE_STEMS = ("treprostinil", "selexipag", "iloprost", "epoprostenol")


class UnknownDrugValues(Exception):
    """The sweep found drug values absent from the vocabulary."""


@dataclass(frozen=True)
class DrugVocabulary:
    """The PAH drug vocabulary, from the dbt seed.

    Lives in transform/seeds/ so Python and dbt read the same file. Nothing
    hardcodes a drug string in code. See ADR-015.
    """

    rows: tuple[dict[str, str], ...]

    @classmethod
    def from_csv(cls, path: Path = DEFAULT_VOCABULARY_PATH) -> DrugVocabulary:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = tuple(dict(r) for r in csv.DictReader(handle))
        if not rows:
            raise ValueError(f"{path} is empty")
        return cls(rows=rows)

    @property
    def generic_names(self) -> tuple[str, ...]:
        """Distinct exact Gnrc_Name values, the filter predicates."""
        return tuple(sorted({r["gnrc_name"] for r in self.rows}))

    @property
    def known_pairs(self) -> frozenset[tuple[str, str]]:
        return frozenset((r["gnrc_name"], r["brnd_name"]) for r in self.rows)


def sweep_for_unknown_drugs(
    client: HttpClient,
    dataset: CmsDataset,
    vocabulary: DrugVocabulary,
    *,
    stems: tuple[str, ...] = MOLECULE_STEMS,
) -> None:
    """Broad keyword sweep, compared against the vocabulary.

    Exact-match filtering can only return what the vocabulary already knows,
    so a new formulation would be omitted and the omission would look exactly
    like a quiet quarter. This raises instead. See ADR-015; the sweep is not
    optional and removing it falsifies that decision.
    """
    unknown: set[tuple[str, str]] = set()

    for stem in stems:
        rows: list[dict[str, Any]] = client.get(
            dataset.data_url, params={"keyword": stem, "size": str(MAX_PAGE_SIZE)}
        ).json()

        for row in rows:
            pair = (row.get("Gnrc_Name", ""), row.get("Brnd_Name", ""))
            if pair not in vocabulary.known_pairs:
                unknown.add(pair)

    if unknown:
        raise UnknownDrugValues(
            f"{dataset.uuid}: {len(unknown)} drug value pairs not in the "
            "vocabulary; review and add to transform/seeds/drug_vocabulary.csv "
            "or confirm they are unrelated matches: "
            + "; ".join(sorted(f"{g!r}/{b!r}" for g, b in unknown))
        )

    log.info("cms.sweep_clean", uuid=dataset.uuid, stems=len(stems))


def _release_year(dataset: CmsDataset) -> str:
    """Release year from `temporal`, not from the title.

    The 2024 pair's title date does not match its temporal endDate, so the
    title is not a reliable source for the partition key.
    """
    temporal = dataset.temporal
    if isinstance(temporal, list) and temporal:
        end = temporal[0].get("endDate", "")
        if len(end) >= 4:
            return end[:4]
    raise ValueError(f"{dataset.uuid}: no usable temporal endDate")


def land_release(
    client: HttpClient,
    dataset: CmsDataset,
    vocabulary: DrugVocabulary,
    writer: LandingWriter,
    *,
    attempt: Attempt,
    dt: date | None = None,
    skip_sweep: bool = False,
) -> tuple[Batch, LandedPart, list[dict[str, Any]]]:
    """Filter to the vocabulary, land one release as a single batch.

    The caller owns the attempt: the CLI for a single --year release, and
    land_all_releases for a backfill, where it must wrap the checkpoint. The
    sweep runs inside it, so an unknown drug (ADR-015) is a failed row that
    names the release.
    """
    year = _release_year(dataset)
    attempt.details.update(release_year=year, dataset_uuid=dataset.uuid)

    if not skip_sweep:
        sweep_for_unknown_drugs(client, dataset, vocabulary)

    batch = Batch.new("cms_part_d", dt=dt)
    attempt.bind_batch(batch)
    bound = log.bind(uuid=dataset.uuid, release_year=year, batch_id=batch.batch_id)

    collected: list[dict[str, Any]] = []
    per_generic: dict[str, int] = {}
    # The same dict object, so a release that fails partway records the
    # generics it had finished.
    attempt.details["rows_per_generic"] = per_generic

    for generic in vocabulary.generic_names:
        filters = {"filter[Gnrc_Name]": generic}
        rows = list(iter_rows(client, dataset, filters=filters))
        attempt.rows_read += len(rows)

        for row in rows:
            # Source-specific provenance, beyond the five LandingWriter adds.
            # Included in the row hash, because the same NPI and brand in a
            # different release year is a different row.
            row["_cms_release_year"] = year
            row["_cms_dataset_uuid"] = dataset.uuid

        collected.extend(rows)
        per_generic[generic] = len(rows)
        bound.info("cms.generic_extracted", generic=generic, rows=len(rows))

    if not collected:
        raise PaginationIncomplete(
            f"{dataset.uuid}: vocabulary matched zero rows; the stored values "
            "have probably changed again (see ADR-015)"
        )

    part = writer.write_part(collected, batch=batch)
    manifest_key = writer.write_manifest(
        batch=batch,
        parts=[part],
        source_query={
            "dataset_uuid": dataset.uuid,
            "dataset_title": dataset.title,
            "release_year": year,
            "filter_column": "Gnrc_Name",
            "generic_names": list(vocabulary.generic_names),
            "rows_per_generic": per_generic,
        },
    )
    attempt.record_landed([part], manifest_key=manifest_key)

    bound.info("cms.release_landed", rows=len(collected), key=part.key)

    return batch, part, collected


def releases_by_year(datasets: list[CmsDataset]) -> dict[str, CmsDataset]:
    """Year to dataset, keyed on temporal endDate rather than the title.

    The 2024 release is registered twice under distinct UUIDs holding
    identical data (verified by row count at plan S12). Duplicates are
    collapsed to the first and warned about; a genuine disagreement would
    show up as two different years and both would be kept.
    """
    by_year: dict[str, CmsDataset] = {}
    for dataset in datasets:
        try:
            year = _release_year(dataset)
        except ValueError:
            log.warning("cms.dataset_without_temporal", uuid=dataset.uuid)
            continue

        if year in by_year:
            log.warning(
                "cms.duplicate_release",
                year=year,
                kept=by_year[year].uuid,
                dropped=dataset.uuid,
            )
            continue
        by_year[year] = dataset

    return dict(sorted(by_year.items()))


def land_all_releases(
    client: HttpClient,
    vocabulary: DrugVocabulary,
    writer: LandingWriter,
    store: StateStore,
    *,
    runlog: RunLog,
    dt: date | None = None,
    full_refresh: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Land every release newer than the checkpoint.

    Checkpointed per release, not per run: a crawl that dies on release nine
    resumes at nine. The watermark is a release year, which sorts
    lexicographically because the years are four-digit and zero-padded.

    Every run that is not a dry run leaves at least one run-log row: one per
    release attempted, one no_new_rows row when nothing is pending, and a
    failed row if catalog discovery itself fails.
    """
    try:
        releases = releases_by_year(discover_datasets(client))
    except Exception:
        if dry_run:
            raise
        # Discovery is not a batch, but a run that dies here must still leave
        # a row. attempt() records the failure and re-raises it.
        with runlog.attempt("cms_part_d", full_refresh=full_refresh):
            raise

    watermark = None if full_refresh else store.read("cms_part_d")
    last_done = watermark.watermark if watermark else None

    pending = {
        year: ds
        for year, ds in releases.items()
        if last_done is None or year > last_done
    }

    log.info(
        "cms.backfill_plan",
        releases=len(releases),
        pending=len(pending),
        last_done=last_done,
        years=list(pending),
    )

    if dry_run:
        return {year: -1 for year in pending}

    if not pending:
        with runlog.attempt("cms_part_d", full_refresh=full_refresh) as attempt:
            attempt.watermark_from = last_done
            attempt.details["releases_seen"] = len(releases)
            attempt.mark_no_new_rows()
        return {}

    landed: dict[str, int] = {}
    for year, dataset in pending.items():
        # One checkpoint per release, so an interruption loses at most one.
        # Attempt outside, checkpoint inside: see ingest/core/runlog.py.
        with runlog.checkpointed_attempt(
            store, "cms_part_d", full_refresh=full_refresh
        ) as (attempt, checkpoint):
            attempt.watermark_from = checkpoint.value
            batch, part, _ = land_release(
                client, dataset, vocabulary, writer, attempt=attempt, dt=dt
            )
            checkpoint.advance(
                year, batch_id=batch.batch_id, allow_regression=full_refresh
            )
            attempt.watermark_to = year
            landed[year] = part.row_count

    return landed
