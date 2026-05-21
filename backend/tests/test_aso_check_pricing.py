from decimal import Decimal

from app.services.aso_check.pricing import (
    PricingProductSnapshot,
    PricingTerritorySnapshot,
    build_pricing_recommendations,
)


def _territory(
    code: str,
    name: str,
    currency: str,
    price: str,
    gdp: str | None,
) -> PricingTerritorySnapshot:
    return PricingTerritorySnapshot(
        territory_code=code,
        territory_name=name,
        currency_code=currency,
        customer_price=Decimal(price),
        gdp_per_capita_usd=Decimal(gdp) if gdp is not None else None,
    )


def test_pricing_recommendations_flag_non_localized_usd_storefronts() -> None:
    product = PricingProductSnapshot(
        kind="subscription",
        app_id=42,
        local_id=7,
        group_id=3,
        name="Pro Monthly",
        product_id="pro.monthly",
        prices=[
            _territory("US", "United States", "USD", "9.99", "85370"),
            _territory("PA", "Panama", "USD", "9.99", "39580"),
            _territory("EC", "Ecuador", "USD", "9.99", "14620"),
            _territory("SV", "El Salvador", "USD", "9.99", "11710"),
            _territory("CA", "Canada", "CAD", "12.99", "60180"),
        ],
    )

    recommendations = build_pricing_recommendations([product])

    assert recommendations
    first = recommendations[0]
    assert first.id == "pricing-usd-clone-subscription-7"
    assert first.category == "pricing"
    assert first.cta_path == "/apps/42/pricing?group=3&sub=7"
    assert "9.99 USD" in first.body
    assert any("Panama" in fact for fact in first.facts)
    assert any("pro.monthly" in fact for fact in first.facts)


def test_pricing_recommendations_flag_large_ppp_gaps() -> None:
    product = PricingProductSnapshot(
        kind="iap",
        app_id=5,
        local_id=9,
        group_id=None,
        name="Starter Pack",
        product_id="starter.pack",
        prices=[
            _territory("US", "United States", "USD", "10.00", "85370"),
            _territory("BR", "Brazil", "BRL", "16.00", "21080"),
            _territory("IN", "India", "INR", "12.00", "11280"),
            _territory("ZA", "South Africa", "ZAR", "10.00", "16370"),
            _territory("TR", "Turkey", "TRY", "11.00", "41020"),
            _territory("MX", "Mexico", "MXN", "15.00", "24970"),
        ],
    )

    recommendations = build_pricing_recommendations([product])

    assert recommendations
    first = recommendations[0]
    assert first.id == "pricing-ppp-gap-iap-9"
    assert first.cta_path == "/apps/5/pricing?tab=iap&iap=9"
    assert ">35%" in " ".join(first.facts)
    assert "PPP-style baseline" in first.title


def test_pricing_recommendations_fall_back_to_sync_when_cache_is_empty() -> None:
    product = PricingProductSnapshot(
        kind="subscription",
        app_id=8,
        local_id=11,
        group_id=4,
        name="Annual Pro",
        product_id="annual.pro",
        prices=[],
    )

    recommendations = build_pricing_recommendations([product])

    assert len(recommendations) == 1
    assert recommendations[0].id == "pricing-sync-subscription-11"
    assert recommendations[0].cta_path == "/apps/8/pricing?group=4&sub=11"
    assert "no cached territory prices" in recommendations[0].body.lower()
