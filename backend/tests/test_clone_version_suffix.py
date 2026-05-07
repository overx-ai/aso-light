"""Tests for the version-suffix increment helper.

Default for a fresh bump is ``.v2`` (dot-style). Pre-existing ``_v{n}``
ids keep their underscore lineage. ``.v{n}`` ids increment in place so
the suffix never compounds (no ``.v2_v2``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.asc.clone import next_versioned_product_id


@pytest.mark.parametrize(
    "given,expected",
    [
        # No suffix -> dot-style default
        ("com.app.pro", "com.app.pro.v2"),
        ("annual", "annual.v2"),
        ("com.app.pro_legacy", "com.app.pro_legacy.v2"),
        # Dot-style increments in place
        ("com.app.pro.v2", "com.app.pro.v3"),
        ("com.app.pro.v9", "com.app.pro.v10"),
        ("refresher.monthly.v2", "refresher.monthly.v3"),
        # Underscore-style preserved for backward compat
        ("com.app.pro_v2", "com.app.pro_v3"),
        ("com.app.pro_v9", "com.app.pro_v10"),
        ("com.app.pro_v99", "com.app.pro_v100"),
        ("monthly_v2", "monthly_v3"),
        ("pkg.weekly_v1", "pkg.weekly_v2"),
    ],
)
def test_version_suffix_increments(given: str, expected: str) -> None:
    assert next_versioned_product_id(given) == expected
