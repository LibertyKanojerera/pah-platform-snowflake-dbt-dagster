"""DuckDB connections that can read the landing zone.

DuckDB's credential chain cannot resolve an SSO profile's cached token, which
is why every ad-hoc query so far has needed credentials exported into the
environment first. boto3 resolves SSO perfectly well, so it resolves them and
passes the concrete values to DuckDB.

That removes the export entirely: no stale environment variables left behind
to shadow AWS_PROFILE for later boto3 calls, which has broken three runs so
far.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import boto3
import duckdb

from ingest.core.config import get_settings
from ingest.core.log import get_logger

log = get_logger(__name__)


@contextmanager
def s3_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """A DuckDB connection authorised to read s3://.

    The credentials are short-lived session credentials interpolated into
    CREATE SECRET, which does not accept bind parameters. They are never
    logged and expire with the session.
    """
    settings = get_settings()
    session = boto3.Session(
        profile_name=settings.aws_profile, region_name=settings.aws_region
    )
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "no AWS credentials resolved; run `aws sso login --profile pah-admin`"
        )
    frozen = credentials.get_frozen_credentials()

    con = duckdb.connect()
    try:
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute(
            f"""
            CREATE SECRET (
                TYPE s3,
                KEY_ID '{frozen.access_key}',
                SECRET '{frozen.secret_key}',
                SESSION_TOKEN '{frozen.token}',
                REGION '{settings.aws_region}'
            )
            """
        )
        yield con
    finally:
        con.close()
