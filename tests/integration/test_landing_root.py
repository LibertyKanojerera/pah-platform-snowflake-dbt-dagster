"""Integration batches never land under the production prefix (ADR-019)."""

from __future__ import annotations

import pytest

from ingest.core.landing import Batch

pytestmark = pytest.mark.integration


def test_integration_batches_land_under_the_test_root() -> None:
    assert Batch.new("cms_part_d").prefix.startswith("landing_test/")
