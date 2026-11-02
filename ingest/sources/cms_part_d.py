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

import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from ingest.core.http import HttpClient
from ingest.core.log import get_logger

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
