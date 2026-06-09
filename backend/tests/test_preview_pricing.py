"""Test preview pricing logic with the shared sync pytest async harness."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

from _async_harness import run_async
from app.services.pricing.currency_rounding import apply_currency_rounding
from app.services.pricing.vat import apply_vat


class _StaticRateClient:
    def __init__(self, rates: dict[str, float]) -> None:
        self._rates = rates
        self.calls: list[str] = []

    async def get_rates(self, base: str = "USD") -> dict[str, float]:
        self.calls.append(base)
        return self._rates


def test_exchange_rate_preview():
    """Simulate the exchange-rate preview branch without live network I/O."""

    async def go() -> tuple[list[dict[str, float | str]], list[str]]:
        base_price = Decimal("2.99")
        base_currency = "USD"
        apply_vat_flag = False
        charming_mode = "smart"
        rate_client = _StaticRateClient(
            {
                "GBP": 0.7450,
                "EUR": 0.8610,
                "JPY": 158.9200,
                "KRW": 1501.6600,
                "BRL": 5.0300,
                "INR": 96.8200,
                "AUD": 1.4000,
                "CAD": 1.3700,
                "MXN": 17.3300,
            }
        )
        rates = await rate_client.get_rates(base=base_currency)
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

        results: list[dict[str, float | str]] = []
        for territory in test_territories:
            currency = territory["currency"]
            rate = 1.0 if currency == base_currency else rates.get(currency)
            if rate is None:
                continue

            suggested = base_price * Decimal(str(rate))
            if apply_vat_flag and territory["vat_rate"] > 0:
                suggested = apply_vat(suggested, territory["vat_rate"])
            if charming_mode == "smart":
                suggested = apply_currency_rounding(suggested, currency)

            results.append(
                {
                    "territory_code": territory["code"],
                    "currency_code": currency,
                    "suggested_price": float(suggested),
                }
            )

        return results, rate_client.calls

    results, calls = run_async(go())

    assert calls == ["USD"]
    assert len(results) == 10

    us_result = next(r for r in results if r["territory_code"] == "US")
    assert abs(us_result["suggested_price"] - 2.99) < 0.001

    jp_result = next(r for r in results if r["territory_code"] == "JP")
    assert jp_result["suggested_price"] == int(jp_result["suggested_price"])

    for result in results:
        assert result["suggested_price"] > 0
