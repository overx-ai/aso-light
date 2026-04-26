"""End-to-end test for the pricing preview endpoint.

Runs against the live database and real exchange rates API,
but the ASC price points fetch will timeout (expected).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.session import async_session_factory, engine
from app.db.base import Base
from app.models.territory import Territory
from app.models.subscription import Subscription, SubscriptionGroup, SubscriptionPrice
from app.services.pricing.currency_rounding import apply_currency_rounding
from app.services.pricing.vat import apply_vat as apply_vat_fn
from app.services.rates.client import RateCacheClient
from decimal import Decimal
from sqlalchemy import select


async def test_preview_logic():
    """Test the exchange rate preview logic directly against the DB."""

    async with async_session_factory() as session:
        # 1. Load territories
        result = await session.execute(select(Territory))
        territories = result.scalars().all()
        territory_map = {t.code: t for t in territories}
        print(f"Loaded {len(territory_map)} territories")

        # 2. Check subscription exists
        sub_result = await session.execute(select(Subscription).where(Subscription.id == 1))
        subscription = sub_result.scalar_one_or_none()
        if subscription is None:
            print("ERROR: Subscription 1 not found")
            return
        print(f"Subscription: {subscription.name} ({subscription.product_id})")

        # 3. Load current prices from DB
        prices_result = await session.execute(
            select(SubscriptionPrice).where(
                SubscriptionPrice.subscription_id == subscription.id
            )
        )
        current_prices = prices_result.scalars().all()
        current_price_by_territory = {p.territory_id: p for p in current_prices}
        print(f"Current prices in DB: {len(current_prices)}")

    # 4. Fetch exchange rates (real API)
    base_price = Decimal("2.99")
    base_territory_code = "US"
    base_territory = territory_map.get(base_territory_code)
    if base_territory is None:
        print(f"ERROR: Base territory '{base_territory_code}' not found")
        print(f"Available codes (first 10): {list(territory_map.keys())[:10]}")
        return
    base_currency = base_territory.currency_code
    print(f"Base: {base_territory_code} / {base_currency}")

    rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
    rates = await rate_client.get_rates(base=base_currency)
    print(f"Fetched {len(rates)} exchange rates")

    # 5. Calculate preview items (same logic as pricing.py exchange_rate branch)
    preview_items = []
    for territory in territory_map.values():
        currency = territory.currency_code
        if currency == base_currency:
            rate = 1.0
        else:
            rate = rates.get(currency)
            if rate is None:
                continue

        suggested_decimal = base_price * Decimal(str(rate))

        # Apply smart rounding
        suggested_decimal = apply_currency_rounding(suggested_decimal, currency)
        suggested = float(suggested_decimal)

        current = current_price_by_territory.get(territory.id)
        current_price = current.customer_price if current else None

        preview_items.append({
            "territory_code": territory.code,
            "territory_name": territory.name,
            "currency_code": currency,
            "current_price": current_price,
            "suggested_price": suggested,
            "nearest_apple_price": None,
            "price_point_id": None,
            "diff_percent": None,
        })

    print(f"\nGenerated {len(preview_items)} preview items")
    print(f"\n{'Territory':<6} {'Currency':<5} {'Suggested':>10} {'Current':>10}")
    print("-" * 40)
    for item in sorted(preview_items, key=lambda x: x["territory_code"])[:20]:
        current = f"${item['current_price']:.2f}" if item["current_price"] else "N/A"
        print(f"{item['territory_code']:<6} {item['currency_code']:<5} ${item['suggested_price']:>9.2f} {current:>10}")
    if len(preview_items) > 20:
        print(f"... and {len(preview_items) - 20} more territories")

    # Assertions
    assert len(preview_items) > 100, f"Expected 100+ territories, got {len(preview_items)}"

    us = next(i for i in preview_items if i["territory_code"] == "US")
    assert abs(us["suggested_price"] - float(base_price)) < 0.10, \
        f"US price should be ~{base_price}, got {us['suggested_price']}"

    print("\nAll assertions passed!")


if __name__ == "__main__":
    asyncio.run(test_preview_logic())
