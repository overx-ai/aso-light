"""Pure helpers for GDP-bracket pricing strategy.

Each territory is assigned to one of four tiers (top/mid/low/special) and the
tier dictates an absolute USD price — unlike PPP/BigMac/etc. which scale
proportionally from a base.

Tier assignment priority (first match wins):
    1. Special list (always wins, even over manual overrides)
    2. Manual override per territory
    3. GDP/capita PPP threshold (top_min, mid_min)
    4. Fallback: low (when GDP data is missing)
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.pricing import GDPBracketConfig, GDPTier


def assign_tier(
    territory_code: str,
    gdp_value: float | Decimal | None,
    config: GDPBracketConfig,
) -> GDPTier:
    """Resolve the tier for a single territory."""
    if territory_code in config.special_territories:
        return "special"
    if territory_code in config.manual_overrides:
        return config.manual_overrides[territory_code]
    if gdp_value is None:
        return "low"

    gdp = Decimal(str(gdp_value))
    thresholds = config.tier_thresholds_usd
    if gdp >= thresholds["top_min"]:
        return "top"
    if gdp >= thresholds["mid_min"]:
        return "mid"
    return "low"
