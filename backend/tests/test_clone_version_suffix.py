"""Tests for the version-suffix increment helper.

The ``next_versioned_product_id`` function is the rule the user picked
in the planning step ("Auto-suffix _v2/_v3"). We pin it here so future
edits don't silently change the suffix shape.
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
        ("com.app.pro", "com.app.pro_v2"),
        ("com.app.pro_v2", "com.app.pro_v3"),
        ("com.app.pro_v9", "com.app.pro_v10"),
        ("com.app.pro_v99", "com.app.pro_v100"),
        ("annual", "annual_v2"),
        ("monthly_v2", "monthly_v3"),
        # Single-digit suffix immediately after dot still works.
        ("pkg.weekly_v1", "pkg.weekly_v2"),
        # Trailing-but-not-version-shaped suffix is preserved.
        ("com.app.pro_legacy", "com.app.pro_legacy_v2"),
    ],
)
def test_version_suffix_increments(given: str, expected: str) -> None:
    assert next_versioned_product_id(given) == expected
