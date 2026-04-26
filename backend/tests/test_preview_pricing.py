"""Test the pricing preview logic in isolation.

Uses real exchange rates from api.overx.ai but mocks the ASC price points
fetch (which is slow and requires credentials).
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.pricing.currency_rounding import apply_currency_rounding
from app.services.pricing.vat import apply_vat
from app.services.rates.client import RateCacheClient


async def test_exchange_rate_preview():
    """Simulate the exchange rate preview branch from pricing.py."""
    base_price = Decimal("2.99")
    base_currency = "USD"
    apply_vat_flag = False
    charming_mode = "smart"

    # 1. Fetch real exchange rates
    print("Fetching exchange rates from api.overx.ai...")
    rate_client = RateCacheClient("https://api.overx.ai")
    rates = await rate_client.get_rates(base=base_currency)
    print(f"Got {len(rates)} rates")

    # 2. Test a few key territories
    test_territories = [
        {"code": "US", "currency": "USD", "vat_rate": 0.0},
        {"code": "GB", "currency": "GBP", "vat_rate": 0.20},
        {"code": "DE", "currency": "EUR", "vat_rate": 0.19},
        {"code": "JP", "currency": "JPY", "vat_rate": 0.10},
        {"code": "KR", "currency": "KRW", "vat_rate": 0.10},
        {"code": "BR", "currency": "BRL", "vat_rate": 0.0},
        {"code": "IN", "currency": "INR", "vat_rate": 0.18},
        {"code": "AU", "currency": "AUD", "vat_rate": 0.10},
        {"code": "CA", "currency": "CAD", "vat_rate": 0.0},
        {"code": "MX", "currency": "MXN", "vat_rate": 0.16},
    ]

    print(f"\n{'Territory':<10} {'Currency':<8} {'Rate':<10} {'Raw':<12} {'VAT':<12} {'Rounded':<12}")
    print("-" * 72)

    results = []
    for t in test_territories:
        currency = t["currency"]
        if currency == base_currency:
            rate = 1.0
        else:
            rate = rates.get(currency)
            if rate is None:
                print(f"{t['code']:<10} {currency:<8} NO RATE")
                continue

        suggested = base_price * Decimal(str(rate))
        raw_price = float(suggested)

        # Apply VAT
        if apply_vat_flag and t["vat_rate"] > 0:
            suggested = apply_vat(suggested, t["vat_rate"])
        vat_price = float(suggested)

        # Apply rounding
        if charming_mode == "smart":
            suggested = apply_currency_rounding(suggested, currency)

        final_price = float(suggested)

        print(
            f"{t['code']:<10} {currency:<8} {rate:<10.4f} "
            f"${raw_price:<11.2f} ${vat_price:<11.2f} ${final_price:<11.2f}"
        )

        results.append({
            "territory_code": t["code"],
            "currency_code": currency,
            "suggested_price": final_price,
        })

    # 3. Assertions
    assert len(results) > 0, "Should have at least one result"

    # US should be exactly base_price (rate = 1.0)
    us_result = next(r for r in results if r["territory_code"] == "US")
    assert us_result["suggested_price"] == float(base_price) or abs(
        us_result["suggested_price"] - float(base_price)
    ) < 0.10, f"US price should be close to {base_price}, got {us_result['suggested_price']}"

    # JP should be a whole number (JPY has 0 decimals)
    jp_result = next(r for r in results if r["territory_code"] == "JP")
    assert jp_result["suggested_price"] == int(
        jp_result["suggested_price"]
    ), f"JPY should be whole number, got {jp_result['suggested_price']}"

    # All prices should be positive
    for r in results:
        assert r["suggested_price"] > 0, f"{r['territory_code']} price should be positive"

    print("\nAll assertions passed!")
    return results


if __name__ == "__main__":
    asyncio.run(test_exchange_rate_preview())
