"""Per-territory currency resolver.

Apple does not always serve a territory in its "natural" local currency
(e.g. Belarus and Ukraine are billed in USD, not BYN/UAH; Bosnia and
Serbia in EUR, not BAM/RSD). The cached price points carry the actual
currency Apple uses; ``territory.currency_code`` is only the local
default we seed at install time. This module is the single place that
decides which one to trust.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.territory import Territory


def effective_currency(
    territory: "Territory",
    cached_price_points: list[dict] | None,
) -> str:
    """Return Apple's actual per-territory currency.

    Cached price points carry Apple's true currency. Fall back to the
    seeded ``territory.currency_code`` only when no cache exists or the
    cached entry has an empty ``currency_code`` (legacy data fetched
    before we requested ``fields[territories]=currency`` from Apple).
    """
    if cached_price_points:
        cur = cached_price_points[0].get("currency_code") or ""
        if cur:
            return cur
    return territory.currency_code
