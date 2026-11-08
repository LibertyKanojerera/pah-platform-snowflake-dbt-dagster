"""NPPES NPI Registry enrichment.

One request per NPI; there is no bulk endpoint. See docs/sources.md.

Three decisions, each forced by something the reconnaissance found.

**A miss is not a failure.** A well-formed but unassigned NPI returns HTTP 200
with `result_count: 0`, so `http.py` cannot distinguish it from a hit. The
branch lives here, and an unfound provider is a nullable enrichment rather
than a pipeline error: CMS prescribing is the fact, NPPES is the dimension.

**Errors arrive in the body.** A bad request returns an `Errors` object, again
without an HTTP status the client will raise on. Checked explicitly below.

**The record is flattened at landing, with the raw response retained.**
NPPES returns a nested document whose useful content requires two choices:
which taxonomy is the specialty, and which address is the practice location.
Those rules encode judgment, so they belong in version-controlled code with
tests rather than being re-expressed in SQL in every model that needs them.
`_raw_response` keeps canonical JSON of the whole record, so nothing is lost
and staging can reach anything this flattening omits.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from ingest.core.cache import JsonCache
from ingest.core.http import HttpClient
from ingest.core.landing import Batch, LandedPart, LandingWriter
from ingest.core.log import get_logger
from ingest.core.throttle import TokenBucket

log = get_logger(__name__)

NPPES_BASE_URL = "https://npiregistry.cms.hhs.gov"
NPPES_PATH = "/api/"
NPPES_VERSION = "2.1"

#: Measured ceiling was ~8.6 req/s sequential with no throttling observed.
#: Running below it deliberately: this is a free public service, the whole
#: workload is ~1,100 requests, and the difference is two minutes.
DEFAULT_RATE_PER_SECOND = 5.0

PROGRESS_EVERY = 100


class NppesApiError(Exception):
    """The API returned an Errors object."""


class TooManyFailures(Exception):
    """Failure rate exceeded the ceiling; the run aborted rather than landing
    a batch that looks complete."""


@dataclass(frozen=True)
class NppesLookup:
    npi: str
    found: bool
    record: dict[str, Any] | None
    error: str = ""
    from_cache: bool = False


#: Above this share of failures the run aborts. A batch where most lookups
#: errored would land with a valid manifest and read as a quiet period, which
#: is the failure mode ADR-010 is about. Checked only after a minimum number
#: of attempts, so one early failure in a ten-NPI run does not abort it.
DEFAULT_MAX_FAILURE_RATE = 0.05
MIN_ATTEMPTS_BEFORE_ABORT = 50


def iter_lookups(
    client: HttpClient,
    npis: Iterable[str],
    *,
    rate: float = DEFAULT_RATE_PER_SECOND,
    cache: JsonCache | None = None,
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE,
) -> Iterator[NppesLookup]:
    bucket = TokenBucket(rate=rate)
    found = missed = failed = cached = 0

    for index, npi in enumerate(npis, start=1):
        entry = cache.get(npi) if cache else None

        if entry is not None:
            cached += 1
            if entry.found:
                found += 1
            else:
                missed += 1
            yield NppesLookup(
                npi=npi, found=entry.found, record=entry.payload, from_cache=True
            )
        else:
            # Only a real request consumes a token. A cache hit costs the
            # server nothing and must not be throttled.
            bucket.acquire()
            try:
                result = lookup_npi(client, npi)
            except Exception as exc:  # noqa: BLE001 - deliberate: see below
                # One provider failing must not discard the other 1,099.
                # NPPES is a dimension; CMS prescribing is the fact.
                failed += 1
                log.warning("nppes.failed", npi=npi, error=str(exc))
                yield NppesLookup(npi=npi, found=False, record=None, error=str(exc))
            else:
                if result.found:
                    found += 1
                else:
                    missed += 1
                    log.warning("nppes.not_found", npi=npi)
                # Hits and misses are cached; failures deliberately are not,
                # so a transient error retries next run instead of becoming a
                # hole that lasts the whole TTL.
                if cache:
                    cache.put(npi, found=result.found, payload=result.record)
                yield result

        if index >= MIN_ATTEMPTS_BEFORE_ABORT and failed / index > max_failure_rate:
            raise TooManyFailures(
                f"{failed} of {index} lookups failed "
                f"({failed / index:.1%} > {max_failure_rate:.1%})"
            )

        if index % PROGRESS_EVERY == 0:
            log.info(
                "nppes.progress",
                processed=index,
                found=found,
                missed=missed,
                failed=failed,
                cached=cached,
            )

    log.info("nppes.complete", found=found, missed=missed, failed=failed, cached=cached)


def _primary_taxonomy(
    taxonomies: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, bool]:
    """Choose the specialty taxonomy. Returns (chosen, ambiguous).

    Specialty is the independent variable of research question one, so a
    silent wrong pick is the most damaging thing this module could do.

    Unambiguous cases: exactly one marked primary, or a single taxonomy with
    no primary flag. Otherwise — several primary, or several with none
    primary — the choice is made deterministically by sorting on the taxonomy
    code, so re-runs agree and the idempotency guarantee holds, and the row is
    flagged. The flag is what a dbt test asserts on at plan S29; the pick is
    never silent.
    """
    if not taxonomies:
        return None, False

    primaries = [t for t in taxonomies if t.get("primary")]

    if len(primaries) == 1:
        return primaries[0], False
    if not primaries and len(taxonomies) == 1:
        return taxonomies[0], False

    pool = primaries or taxonomies
    return min(pool, key=lambda t: str(t.get("code", ""))), True


def _location_address(addresses: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The practice location, not the mailing address.

    A mailing address can be a billing office in another state, which would
    distort any geographic cut.
    """
    for address in addresses or []:
        if address.get("address_purpose") == "LOCATION":
            return address
    return None


def flatten(lookup: NppesLookup) -> dict[str, Any]:
    if not lookup.found or lookup.record is None:
        return {
            "npi": lookup.npi,
            "nppes_found": False,
            "nppes_error": lookup.error,
            "_raw_response": "",
        }

    record = lookup.record
    basic: Mapping[str, Any] = record.get("basic", {}) or {}
    chosen, ambiguous = _primary_taxonomy(record.get("taxonomies", []) or [])
    location = _location_address(record.get("addresses", []) or [])
    postal = str((location or {}).get("postal_code", ""))

    return {
        "npi": lookup.npi,
        "nppes_found": True,
        "nppes_error": "",
        "enumeration_type": record.get("enumeration_type", ""),
        "status": basic.get("status", ""),
        "credential": basic.get("credential", ""),
        "first_name": basic.get("first_name", ""),
        "middle_name": basic.get("middle_name", ""),
        "last_name": basic.get("last_name", ""),
        "name_prefix": basic.get("name_prefix", ""),
        "organization_name": basic.get("organization_name", ""),
        "sole_proprietor": basic.get("sole_proprietor", ""),
        "enumeration_date": basic.get("enumeration_date", ""),
        "last_updated": basic.get("last_updated", ""),
        "certification_date": basic.get("certification_date", ""),
        "taxonomy_code": (chosen or {}).get("code", ""),
        "taxonomy_desc": (chosen or {}).get("desc", ""),
        "taxonomy_license": (chosen or {}).get("license", ""),
        "taxonomy_state": (chosen or {}).get("state", ""),
        "taxonomy_count": len(record.get("taxonomies", []) or []),
        "taxonomy_ambiguous": ambiguous,
        "location_address_1": (location or {}).get("address_1", ""),
        "location_city": (location or {}).get("city", ""),
        "location_state": (location or {}).get("state", ""),
        "location_postal_code_full": postal,
        # Unformatted ZIP+4 with no hyphen; five characters is what joins.
        "location_postal_code": postal[:5],
        "location_missing": location is None,
        # Canonical so the row hash is stable across key ordering.
        "_raw_response": json.dumps(record, sort_keys=True, separators=(",", ":")),
    }


def lookup_npi(client: HttpClient, npi: str) -> NppesLookup:
    payload = client.get(
        NPPES_PATH, params={"version": NPPES_VERSION, "number": npi}
    ).json()

    if isinstance(payload, dict) and "Errors" in payload:
        raise NppesApiError(f"{npi}: {payload['Errors']}")

    count = int(payload.get("result_count", 0))
    if count == 0:
        return NppesLookup(npi=npi, found=False, record=None)

    return NppesLookup(npi=npi, found=True, record=payload["results"][0])


def land_npis(
    client: HttpClient,
    npis: Iterable[str],
    writer: LandingWriter,
    *,
    dt: date | None = None,
    rate: float = DEFAULT_RATE_PER_SECOND,
    cache: JsonCache | None = None,
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE,
    return_rows: bool = False,
) -> tuple[Batch, LandedPart] | tuple[Batch, LandedPart, list[dict[str, Any]]]:
    batch = Batch.new("nppes", dt=dt)
    rows = [
        flatten(result)
        for result in iter_lookups(
            client,
            npis,
            rate=rate,
            cache=cache,
            max_failure_rate=max_failure_rate,
        )
    ]

    if not rows:
        raise ValueError("no NPIs supplied")

    part = writer.write_part(rows, batch=batch)
    writer.write_manifest(
        batch=batch,
        parts=[part],
        source_query={
            "api_version": NPPES_VERSION,
            "rate_per_second": rate,
            "requested": len(rows),
            "found": sum(1 for r in rows if r["nppes_found"]),
            "failed": sum(1 for r in rows if r.get("nppes_error")),
            "missed": sum(
                1 for r in rows if not r["nppes_found"] and not r.get("nppes_error")
            ),
            "ambiguous_taxonomy": sum(1 for r in rows if r.get("taxonomy_ambiguous")),
        },
    )

    log.info("nppes.landed", key=part.key, rows=len(rows))
    return (batch, part, rows) if return_rows else (batch, part)
