from __future__ import annotations

import json
import time

import pytest

from ingest.core.throttle import TokenBucket
from ingest.sources.nppes import (
    NppesApiError,
    NppesLookup,
    _location_address,
    _primary_taxonomy,
    flatten,
    lookup_npi,
)
from tests.conftest import FakeHttpClient


def _tax(code: str, primary: bool = False, desc: str = "") -> dict:
    return {
        "code": code,
        "desc": desc or code,
        "primary": primary,
        "license": "",
        "state": "MN",
    }


# --- taxonomy resolution -------------------------------------------------
#
# Specialty is the independent variable of research question one, so a wrong
# pick here is the most damaging thing this module can do. 60% of observed
# providers carry several taxonomies, so this path is heavily used; none has
# yet been ambiguous, so only these tests exercise the fallback.


def test_single_primary_is_chosen() -> None:
    chosen, ambiguous = _primary_taxonomy(
        [_tax("A", primary=False), _tax("B", primary=True), _tax("C")]
    )
    assert chosen["code"] == "B"
    assert ambiguous is False


def test_lone_taxonomy_without_primary_flag_is_chosen() -> None:
    chosen, ambiguous = _primary_taxonomy([_tax("A")])
    assert chosen["code"] == "A"
    assert ambiguous is False


def test_several_primaries_are_flagged_ambiguous() -> None:
    chosen, ambiguous = _primary_taxonomy(
        [_tax("Z", primary=True), _tax("A", primary=True)]
    )
    assert ambiguous is True
    assert chosen["code"] == "A", "must pick the lowest code, not input order"


def test_several_taxonomies_with_no_primary_are_flagged_ambiguous() -> None:
    chosen, ambiguous = _primary_taxonomy([_tax("Z"), _tax("A")])
    assert ambiguous is True
    assert chosen["code"] == "A"


def test_ambiguous_choice_is_deterministic() -> None:
    """Re-runs must agree, or the idempotency guarantee breaks: the same
    provider would hash differently between extractions.
    """
    forward = [_tax("Z", primary=True), _tax("A", primary=True)]
    first, _ = _primary_taxonomy(forward)
    second, _ = _primary_taxonomy(list(reversed(forward)))
    assert first["code"] == second["code"]


def test_chosen_taxonomy_is_a_mapping_not_a_list() -> None:
    """Regression: the ambiguous branch once returned the sorted list."""
    chosen, _ = _primary_taxonomy([_tax("Z"), _tax("A")])
    assert isinstance(chosen, dict)


def test_no_taxonomies_returns_none_without_flagging() -> None:
    chosen, ambiguous = _primary_taxonomy([])
    assert chosen is None
    assert ambiguous is False


# --- address selection ---------------------------------------------------


def test_location_is_preferred_over_mailing() -> None:
    """A mailing address can be a billing office in another state."""
    addresses = [
        {"address_purpose": "MAILING", "city": "AUSTIN", "state": "TX"},
        {"address_purpose": "LOCATION", "city": "DULUTH", "state": "MN"},
    ]
    assert _location_address(addresses)["state"] == "MN"


def test_missing_location_returns_none() -> None:
    assert _location_address([{"address_purpose": "MAILING", "city": "AUSTIN"}]) is None


# --- flatten -------------------------------------------------------------


def _record() -> dict:
    return {
        "enumeration_type": "NPI-1",
        "basic": {"first_name": "JOSEPH", "last_name": "MARTINELLI", "status": "A"},
        "taxonomies": [_tax("207RP1001X", primary=True, desc="Pulmonary Disease")],
        "addresses": [
            {
                "address_purpose": "LOCATION",
                "address_1": "920 E 1ST ST",
                "city": "DULUTH",
                "state": "MN",
                "postal_code": "558052201",
            }
        ],
    }


def test_flatten_handles_a_miss_without_raising() -> None:
    """A miss is a nullable enrichment, not a failure: CMS prescribing is the
    fact, NPPES is the dimension. 5 of 1,100 observed NPIs are unassigned.
    """
    row = flatten(NppesLookup(npi="1234567893", found=False, record=None))
    assert row["npi"] == "1234567893"
    assert row["nppes_found"] is False


def test_flatten_truncates_zip_plus_four() -> None:
    """postal_code arrives as unformatted ZIP+4 with no hyphen."""
    row = flatten(NppesLookup(npi="1", found=True, record=_record()))
    assert row["location_postal_code"] == "55805"
    assert row["location_postal_code_full"] == "558052201"


def test_flatten_carries_the_canonical_raw_response() -> None:
    """Raw is retained so staging can reach anything flattening omits, and is
    canonicalised so the row hash is stable across key ordering.
    """
    row = flatten(NppesLookup(npi="1", found=True, record=_record()))
    assert json.loads(row["_raw_response"]) == _record()

    shuffled = dict(reversed(list(_record().items())))
    other = flatten(NppesLookup(npi="1", found=True, record=shuffled))
    assert row["_raw_response"] == other["_raw_response"]


# --- API response handling -----------------------------------------------


def test_errors_in_the_body_raise() -> None:
    """A bad request returns an Errors object with no status the HTTP client
    will raise on, so the check has to live here.
    """
    client = FakeHttpClient(
        lambda p, q: {"Errors": [{"description": "Unsupported Version"}]}
    )
    with pytest.raises(NppesApiError):
        lookup_npi(client, "1003867573")


def test_zero_result_count_is_a_miss_not_an_error() -> None:
    """HTTP 200 with result_count 0 is how NPPES says 'no such provider'."""
    client = FakeHttpClient(lambda p, q: {"result_count": 0, "results": []})
    assert lookup_npi(client, "1234567893").found is False


def test_a_hit_returns_the_first_result() -> None:
    client = FakeHttpClient(lambda p, q: {"result_count": 1, "results": [_record()]})
    result = lookup_npi(client, "1003867573")
    assert result.found is True
    assert result.record["enumeration_type"] == "NPI-1"


def test_version_is_sent_on_every_request() -> None:
    """version is mandatory; omitting it returns Unsupported Version."""
    client = FakeHttpClient(lambda p, q: {"result_count": 0, "results": []})
    lookup_npi(client, "1003867573")
    _path, params = client.calls[0]
    assert params["version"] == "2.1"
    assert params["number"] == "1003867573"


# --- throttle ------------------------------------------------------------


def test_bucket_allows_a_burst_up_to_capacity() -> None:
    bucket = TokenBucket(rate=50, capacity=5)
    start = time.monotonic()
    for _ in range(5):
        bucket.acquire()
    assert time.monotonic() - start < 0.05


def test_bucket_throttles_beyond_capacity() -> None:
    """capacity=1 so only the first acquire is free; the next ten each wait
    1/rate. At rate=50 that is ~0.2s, which keeps the test fast while still
    proving the limiter does something.
    """
    bucket = TokenBucket(rate=50, capacity=1)
    start = time.monotonic()
    for _ in range(11):
        bucket.acquire()
    elapsed = time.monotonic() - start
    assert 0.15 < elapsed < 1.0, elapsed


def test_bucket_rejects_a_request_larger_than_capacity() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=10, capacity=5).acquire(6)


def test_bucket_rejects_a_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
