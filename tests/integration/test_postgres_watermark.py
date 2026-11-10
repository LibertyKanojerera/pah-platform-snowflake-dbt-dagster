"""The test ADR-018 owes.

A row whose event_timestamp is far behind the watermark but whose created_at
is ahead of it must be extracted. On well-behaved data an event_timestamp
watermark and a created_at watermark produce identical results, so this is the
only test that tells them apart.

Requires a running Postgres. Run with `pytest -m integration`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from ingest.sources.app_postgres import TABLES, _dsn, _iter_rows

pytestmark = pytest.mark.integration

LATE_EVENT_ID = 999_000_001
FRESH_EVENT_ID = 999_000_002

LATE_EVENT_TIME = datetime(2019, 1, 2, tzinfo=timezone.utc)

EVENTS = next(t for t in TABLES if t.name == "patient_events")


def _skip_without_credentials() -> None:
    if not os.environ.get("POSTGRES_PASSWORD") and not os.environ.get("AWS_PROFILE"):
        pytest.skip("no Postgres credentials available")


@pytest.fixture
def late_row() -> int:
    """Insert a late-arriving row and remove it afterwards.

    created_at is backdated past SAFETY_LAG. A row stamped now() would sit
    inside the lag window and be correctly withheld, which is what
    test_the_safety_lag_excludes_very_recent_rows asserts.
    """
    _skip_without_credentials()

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "DELETE FROM patient_events WHERE event_id = %s", (LATE_EVENT_ID,)
            )
            cur.execute("SELECT min(patient_id) AS pid FROM patients")
            patient_id = cur.fetchone()["pid"]
            cur.execute(
                """
                INSERT INTO patient_events
                    (event_id, patient_id, event_type, event_timestamp,
                     route, days_supply, created_at)
                VALUES (%s, %s, 'fill', %s, 'oral', 30,
                        now() - interval '5 minutes')
                """,
                (LATE_EVENT_ID, patient_id, LATE_EVENT_TIME),
            )
        conn.commit()

    yield LATE_EVENT_ID

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM patient_events WHERE event_id = %s", (LATE_EVENT_ID,)
            )
        conn.commit()


def test_a_late_arriving_row_is_extracted(late_row: int) -> None:
    """The decision in ADR-018, asserted directly."""
    watermark = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with psycopg.connect(_dsn()) as conn:
        ids = {r["event_id"] for r in _iter_rows(conn, EVENTS, watermark)}

    assert (
        late_row in ids
    ), "late-arriving row was not extracted; the created_at watermark is not working"


def test_an_event_time_watermark_would_have_missed_it(late_row: int) -> None:
    """The rejected alternative, asserted to fail.

    Without this, nothing in the suite shows the decision was load-bearing
    rather than cosmetic.
    """
    event_time_watermark = datetime(2023, 1, 1, tzinfo=timezone.utc)

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT event_id FROM patient_events WHERE event_timestamp > %s",
                (event_time_watermark,),
            )
            ids = {r["event_id"] for r in cur}

    assert late_row not in ids, (
        "the late row is newer than the event-time watermark, so this test "
        "proves nothing; pick an older LATE_EVENT_TIME"
    )


def test_the_safety_lag_excludes_very_recent_rows() -> None:
    """A row inserted moments ago falls inside the lag window and is held back
    until the next run, which is the intended behaviour.
    """
    _skip_without_credentials()

    watermark = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            # A run that died before cleanup would otherwise leave this id
            # behind and fail every later run on the primary key rather than
            # on the property under test.
            cur.execute(
                "DELETE FROM patient_events WHERE event_id = %s", (FRESH_EVENT_ID,)
            )
            cur.execute(
                """
                INSERT INTO patient_events
                    (event_id, patient_id, event_type, event_timestamp,
                     route, days_supply, created_at)
                SELECT %s, min(patient_id), 'fill', now(), 'oral', 30, now()
                FROM patients
                """,
                (FRESH_EVENT_ID,),
            )
        conn.commit()

        try:
            ids = {r["event_id"] for r in _iter_rows(conn, EVENTS, watermark)}
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM patient_events WHERE event_id = %s",
                    (FRESH_EVENT_ID,),
                )
            conn.commit()

    assert FRESH_EVENT_ID not in ids, "safety lag did not hold back a fresh row"
