from __future__ import annotations

from decimal import Decimal

from app.services.growth.recommendations import (
    LocalizedUsdPricingRule,
    MissingPricingCacheRule,
    PricingProductSnapshot,
    TerritoryPriceSnapshot,
)


def _price(
    territory_code: str,
    customer_price: str,
    gdp_per_capita_usd: str,
) -> TerritoryPriceSnapshot:
    return TerritoryPriceSnapshot(
        territory_code=territory_code,
        territory_name=territory_code,
        currency_code="USD",
        customer_price=Decimal(customer_price),
        gdp_per_capita_usd=Decimal(gdp_per_capita_usd),
    )


def test_localized_usd_pricing_rule_flags_matching_lower_gdp_usd_prices():
    product = PricingProductSnapshot(
        kind="subscription",
        id=12,
        group_id=7,
        name="Monthly",
        product_id="monthly.pro",
        prices=(
            _price("US", "9.99", "80000"),
            _price("EC", "9.99", "15000"),
            _price("PA", "9.99", "35000"),
        ),
    )

    recommendations = LocalizedUsdPricingRule().evaluate(
        app_id=42,
        product=product,
    )

    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation.category == "pricing"
    assert recommendation.severity == "warning"
    assert recommendation.cta_path == "/apps/42/pricing?group=7&sub=12"
    assert "2 lower-income USD storefronts" in recommendation.description


def test_missing_pricing_cache_rule_links_iaps_to_existing_pricing_workflow():
    product = PricingProductSnapshot(
        kind="iap",
        id=33,
        group_id=None,
        name="Lifetime",
        product_id="lifetime.unlock",
        prices=(),
    )

    recommendations = MissingPricingCacheRule().evaluate(
        app_id=42,
        product=product,
    )

    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation.category == "pricing"
    assert recommendation.cta_path == "/apps/42/pricing?tab=iap&iap=33"
