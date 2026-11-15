"""Integration tests land under landing_test/, never landing/.

Without this, every integration run added batches to the production prefix:
the repeated copies of the 2023 release were exactly that (S20 audit,
finding 1, ADR-019). landing_test/ has its own 7-day lifecycle rule.

Module scope, not session: a session fixture would leave LANDING_ROOT set for
any unit tests that run after the integration directory in the same session.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from ingest.core.config import get_settings


@pytest.fixture(autouse=True, scope="module")
def _land_under_the_test_root() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LANDING_ROOT", "landing_test")
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()
