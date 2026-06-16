"""Price-apply safety band — a single Decimal source of truth.

The ±50% guard ("skip a territory if the new price differs from the
current price by more than this fraction in either direction") was
previously duplicated three times across the subscription/IAP apply
paths and the MCP tools, each computing in float. This module holds the
band constants and the one comparison helper so the policy is enforced
identically everywhere, in Decimal.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.pricing import PriceApplySkippedItem

# Skip territories where the new price differs from the current price by
# more than this fraction in either direction.
SAFETY_BAND_PCT = Decimal("0.50")
SAFETY_MAX_UP = Decimal("1") + SAFETY_BAND_PCT
SAFETY_MAX_DOWN = Decimal("1") - SAFETY_BAND_PCT
SAFETY_LABEL = f"±{int(SAFETY_BAND_PCT * 100)}%"


def exceeds_safety_band(
    current: Decimal | float | int,
    new: Decimal | float | int,
) -> bool:
    """Return True if ``new`` is more than ±50% away from ``current``.

    Computed in Decimal to remove float nondeterminism. The boundary is
    strict: exactly ±50% is allowed (returns False), just-over is not.
    """
    current_dec = current if isinstance(current, Decimal) else Decimal(str(current))
    new_dec = new if isinstance(new, Decimal) else Decimal(str(new))
    return (
        new_dec > current_dec * SAFETY_MAX_UP
        or new_dec < current_dec * SAFETY_MAX_DOWN
    )


def safety_skip_item(
    territory_code: str,
    *,
    current_price: float | None,
    new_price: float,
    force: bool,
) -> PriceApplySkippedItem | None:
    """Build a skip record if ``new_price`` trips the ±50% band, else None.

    Returns None when the territory should be applied: ``force`` opted out,
    no current price to compare against, or the change is within the band.
    Shared by the subscription/IAP apply paths (REST router and MCP tools)
    so the skip reason text and diff math stay identical everywhere.
    """
    if force or current_price is None or current_price <= 0:
        return None
    if not exceeds_safety_band(current_price, new_price):
        return None
    diff_pct = round(((new_price - current_price) / current_price) * 100, 2)
    return PriceApplySkippedItem(
        territory_code=territory_code,
        reason=f"Price change {diff_pct:+}% exceeds safety limit ({SAFETY_LABEL})",
        current_price=current_price,
        new_price=new_price,
        diff_percent=diff_pct,
    )
