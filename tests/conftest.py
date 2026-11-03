"""Test fixtures.

The HTTP and S3 stubs are hand-rolled rather than moto or responses. They are
about twenty lines, add no dependency, and make the fake's behaviour visible
in the same file as the tests that rely on it.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import pytest


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeHttpClient:
    """Routes requests to a handler and records what was asked for."""

    def __init__(self, handler: Callable[[str, Mapping[str, Any]], Any]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> FakeResponse:
        params = dict(params or {})
        self.calls.append((path, params))
        return FakeResponse(self._handler(path, params))


class FakeS3:
    """Enough of the boto3 S3 client for LandingWriter."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> None:
        self.objects[f"{Bucket}/{Key}"] = Body


@pytest.fixture
def fake_s3() -> FakeS3:
    return FakeS3()
