"""Configuration resolution.

Secrets come from AWS Secrets Manager when AWS_PROFILE is set, and from the
local .env otherwise. The local path exists so the project runs with no cloud
account at all, which is the same portability argument as the DuckDB target.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PostgresConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    dbname: str = "pah"
    user: str = "pah"
    password: SecretStr


class Settings(BaseSettings):
    """Non-secret configuration, from the environment or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    project: str = "pah-platform"

    landing_bucket: str = Field(default="", description="S3 bucket for landed objects.")

    @property
    def use_secrets_manager(self) -> bool:
        return bool(self.aws_profile)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=8)
def _fetch_secret(name: str) -> dict[str, Any]:
    """Read one secret from Secrets Manager. Cached: these are not free calls."""
    import boto3  # imported here so the local path needs no AWS SDK at import time

    settings = get_settings()
    client = boto3.session.Session(
        profile_name=settings.aws_profile,
        region_name=settings.aws_region,
    ).client("secretsmanager")

    response = client.get_secret_value(SecretId=name)
    parsed: dict[str, Any] = json.loads(response["SecretString"])
    return parsed


def get_postgres_config() -> PostgresConfig:
    settings = get_settings()

    if settings.use_secrets_manager:
        return PostgresConfig(**_fetch_secret(f"{settings.project}/postgres/app"))

    return PostgresConfig(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "pah"),
        user=os.environ.get("POSTGRES_USER", "pah"),
        password=SecretStr(os.environ["POSTGRES_PASSWORD"]),
    )
