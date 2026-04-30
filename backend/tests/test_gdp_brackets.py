"""Tests for the GDP-bracket pricing strategy: tier assignment and validators."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.pricing import GDPBracketConfig, PricePreviewRequest
from app.services.pricing.gdp_brackets import assign_tier


def _make_config(
    *,
    top: str = "9.99",
    mid: str = "4.99",
    low: str = "1.99",
    special: str = "2.99",
    top_min: str = "40000",
    mid_min: str = "15000",
    manual_overrides: dict[str, str] | None = None,
    special_territories: list[str] | None = None,
) -> GDPBracketConfig:
    return GDPBracketConfig(
        tier_prices_usd={
            "top": Decimal(top),
            "mid": Decimal(mid),
            "low": Decimal(low),
            "special": Decimal(special),
        },
        tier_thresholds_usd={
            "top_min": Decimal(top_min),
            "mid_min": Decimal(mid_min),
        },
        manual_overrides=manual_overrides or {},
        special_territories=special_territories or [],
    )


# ---------------------------------------------------------------------------
# assign_tier
# ---------------------------------------------------------------------------


def test_threshold_top():
    cfg = _make_config()
    assert assign_tier("US", 70000, cfg) == "top"


def test_threshold_mid():
    cfg = _make_config()
    assert assign_tier("BR", 18000, cfg) == "mid"


def test_threshold_low():
    cfg = _make_config()
    assert assign_tier("IN", 8000, cfg) == "low"


def test_threshold_top_inclusive_boundary():
    cfg = _make_config()
    assert assign_tier("XX", 40000, cfg) == "top"


def test_threshold_mid_inclusive_boundary():
    cfg = _make_config()
    assert assign_tier("XX", 15000, cfg) == "mid"


def test_threshold_just_below_mid_is_low():
    cfg = _make_config()
    assert assign_tier("XX", 14999, cfg) == "low"


def test_missing_gdp_falls_back_to_low():
    cfg = _make_config()
    assert assign_tier("ZZ", None, cfg) == "low"


def test_special_overrides_high_gdp():
    cfg = _make_config(special_territories=["PL"])
    # Poland would be top by GDP, but special list wins
    assert assign_tier("PL", 50000, cfg) == "special"


def test_special_overrides_manual():
    cfg = _make_config(
        special_territories=["RU"],
        manual_overrides={"RU": "top"},
    )
    assert assign_tier("RU", 30000, cfg) == "special"


def test_manual_override_beats_threshold():
    cfg = _make_config(manual_overrides={"JP": "low"})
    assert assign_tier("JP", 45000, cfg) == "low"


def test_manual_override_with_missing_gdp():
    cfg = _make_config(manual_overrides={"AQ": "top"})
    assert assign_tier("AQ", None, cfg) == "top"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_inverted_thresholds_rejected():
    with pytest.raises(ValidationError, match="top_min"):
        GDPBracketConfig(
            tier_prices_usd={
                "top": Decimal("9.99"), "mid": Decimal("4.99"),
                "low": Decimal("1.99"), "special": Decimal("2.99"),
            },
            tier_thresholds_usd={
                "top_min": Decimal("10000"),
                "mid_min": Decimal("20000"),
            },
        )


def test_missing_tier_price_rejected():
    with pytest.raises(ValidationError, match="Missing tier prices"):
        GDPBracketConfig(
            tier_prices_usd={
                "top": Decimal("9.99"),
                "mid": Decimal("4.99"),
                "low": Decimal("1.99"),
                # missing 'special'
            },
            tier_thresholds_usd={
                "top_min": Decimal("40000"),
                "mid_min": Decimal("15000"),
            },
        )


def test_negative_tier_price_rejected():
    with pytest.raises(ValidationError, match="must be > 0"):
        GDPBracketConfig(
            tier_prices_usd={
                "top": Decimal("9.99"), "mid": Decimal("4.99"),
                "low": Decimal("0"), "special": Decimal("2.99"),
            },
            tier_thresholds_usd={
                "top_min": Decimal("40000"),
                "mid_min": Decimal("15000"),
            },
        )


def test_invalid_alpha2_rejected():
    with pytest.raises(ValidationError, match="alpha-2"):
        GDPBracketConfig(
            tier_prices_usd={
                "top": Decimal("9.99"), "mid": Decimal("4.99"),
                "low": Decimal("1.99"), "special": Decimal("2.99"),
            },
            tier_thresholds_usd={
                "top_min": Decimal("40000"),
                "mid_min": Decimal("15000"),
            },
            special_territories=["USA"],  # alpha-3 not allowed
        )


def test_preview_request_requires_gdp_config_for_brackets():
    with pytest.raises(ValidationError, match="gdp_config is required"):
        PricePreviewRequest(index_type="gdp_brackets")


def test_preview_request_accepts_legacy_strategies_without_gdp_config():
    # Should not raise
    req = PricePreviewRequest(index_type="ppp", base_price=9.99)
    assert req.gdp_config is None


def test_lowercase_alpha2_normalized_to_uppercase():
    """Lowercase territory codes are accepted and normalized to upper."""
    cfg = _make_config(
        manual_overrides={"jp": "low"},
        special_territories=["pl", "ru"],
    )
    assert "JP" in cfg.manual_overrides
    assert "jp" not in cfg.manual_overrides
    assert cfg.special_territories == ["PL", "RU"]
    # And tier assignment with the canonical upper code now matches.
    assert assign_tier("JP", 45000, cfg) == "low"
    assert assign_tier("PL", 50000, cfg) == "special"
