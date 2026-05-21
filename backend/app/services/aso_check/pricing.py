"""Pricing recommendation helpers for the ASO / growth advisor surfaces.

These helpers stay pure: the route / MCP layers load cached pricing rows and
territory metadata, then pass in compact snapshots. That keeps the rules easy to
extend and unit test.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Callable, Literal, Mapping
from urllib.parse import urlencode

if TYPE_CHECKING:
    from app.models.iap import InAppPurchase
    from app.models.subscription import SubscriptionGroup
    from app.models.territory import Territory

RecommendationCategory = Literal["pricing"]
RecommendationPriority = Literal["high", "medium", "low"]
ProductKind = Literal["subscription", "iap"]

_PRICE_QUANTUM = Decimal("0.01")
_PRICE_MATCH_TOLERANCE = Decimal("0.01")
_LOW_GDP_USD_RATIO = Decimal("0.80")
_PPP_GAP_THRESHOLD = Decimal("0.35")
_MIN_PPP_GAP_TERRITORIES = 4
_MIN_PRICE_ROWS_FOR_CONFIDENCE = 12
_MAX_FACT_TERRITORIES = 3


@dataclass(frozen=True)
class PricingTerritorySnapshot:
    territory_code: str
    territory_name: str
    currency_code: str
    customer_price: Decimal
    gdp_per_capita_usd: Decimal | None


@dataclass(frozen=True)
class PricingProductSnapshot:
    kind: ProductKind
    app_id: int
    local_id: int
    group_id: int | None
    name: str
    product_id: str
    prices: list[PricingTerritorySnapshot]


@dataclass(frozen=True)
class PricingRecommendation:
    id: str
    category: RecommendationCategory
    priority: RecommendationPriority
    title: str
    body: str
    facts: list[str]
    cta_label: str | None = None
    cta_path: str | None = None


@dataclass(frozen=True)
class _ScoredRecommendation:
    score: Decimal
    recommendation: PricingRecommendation


def build_pricing_snapshots(
    *,
    app_id: int,
    subscription_groups: list[SubscriptionGroup],
    iaps: list[InAppPurchase],
    territory_by_id: Mapping[int, Territory],
) -> list[PricingProductSnapshot]:
    """Flatten cached subscription/IAP prices into app-level snapshots."""
    snapshots: list[PricingProductSnapshot] = []

    for group in subscription_groups:
        for subscription in group.subscriptions:
            prices = _collect_prices(subscription.prices, territory_by_id)
            snapshots.append(
                PricingProductSnapshot(
                    kind="subscription",
                    app_id=app_id,
                    local_id=subscription.id,
                    group_id=group.id,
                    name=subscription.name,
                    product_id=subscription.product_id,
                    prices=prices,
                )
            )

    for iap in iaps:
        prices = _collect_prices(iap.prices, territory_by_id)
        snapshots.append(
            PricingProductSnapshot(
                kind="iap",
                app_id=app_id,
                local_id=iap.id,
                group_id=None,
                name=iap.name,
                product_id=iap.product_id,
                prices=prices,
            )
        )

    return snapshots


def build_pricing_recommendations(
    products: list[PricingProductSnapshot],
) -> list[PricingRecommendation]:
    """Generate pricing recommendations from cached product prices."""
    recommendations: list[PricingRecommendation] = []

    usd_candidate = _best_candidate(products, _build_usd_clone_recommendation)
    if usd_candidate is not None:
        recommendations.append(usd_candidate.recommendation)

    ppp_candidate = _best_candidate(products, _build_ppp_gap_recommendation)
    if ppp_candidate is not None:
        recommendations.append(ppp_candidate.recommendation)

    if recommendations:
        return recommendations

    fallback = _build_sync_or_coverage_recommendation(products)
    return [fallback] if fallback is not None else []


def _collect_prices(price_rows: list[object], territory_by_id: Mapping[int, Territory]) -> list[PricingTerritorySnapshot]:
    snapshots: list[PricingTerritorySnapshot] = []
    for row in price_rows:
        territory = territory_by_id.get(row.territory_id)
        if territory is None:
            continue
        snapshots.append(
            PricingTerritorySnapshot(
                territory_code=territory.code,
                territory_name=territory.name,
                currency_code=territory.currency_code,
                customer_price=_to_decimal(row.customer_price),
                gdp_per_capita_usd=(
                    _to_decimal(territory.gdp_per_capita_usd)
                    if territory.gdp_per_capita_usd is not None
                    else None
                ),
            )
        )
    return sorted(snapshots, key=lambda item: item.territory_code)


def _best_candidate(
    products: list[PricingProductSnapshot],
    builder: Callable[[PricingProductSnapshot], _ScoredRecommendation | None],
) -> _ScoredRecommendation | None:
    candidates = [builder(product) for product in products]
    scored = [candidate for candidate in candidates if candidate is not None]
    if not scored:
        return None
    return max(scored, key=lambda item: item.score)


def _build_usd_clone_recommendation(
    product: PricingProductSnapshot,
) -> _ScoredRecommendation | None:
    by_code = {row.territory_code: row for row in product.prices}
    us_row = by_code.get("US")
    if us_row is None or us_row.gdp_per_capita_usd is None:
        return None

    affected: list[PricingTerritorySnapshot] = []
    for row in product.prices:
        if row.territory_code == "US" or row.currency_code != "USD":
            continue
        if row.gdp_per_capita_usd is None:
            continue
        if row.gdp_per_capita_usd >= us_row.gdp_per_capita_usd * _LOW_GDP_USD_RATIO:
            continue
        if abs(row.customer_price - us_row.customer_price) <= _PRICE_MATCH_TOLERANCE:
            affected.append(row)

    if not affected:
        return None

    affected.sort(
        key=lambda row: row.gdp_per_capita_usd or Decimal("0"),
    )
    fact_names = _territory_list(affected)
    count = len(affected)
    noun = "storefront" if count == 1 else "storefronts"

    recommendation = PricingRecommendation(
        id=f"pricing-usd-clone-{product.kind}-{product.local_id}",
        category="pricing",
        priority="high" if count >= 3 else "medium",
        title=f"{product.name} still mirrors the US USD tier",
        body=(
            f"{count} non-US USD {noun} still use the same "
            f"{_format_price(us_row.customer_price, us_row.currency_code)} as the "
            "US. That is usually a sign the price was copied instead of localized "
            "for local purchasing power."
        ),
        facts=[
            f"US anchor: {_format_price(us_row.customer_price, us_row.currency_code)}",
            f"Affected: {fact_names}",
            f"Product: {product.product_id}",
        ],
        cta_label=_cta_label(product),
        cta_path=_cta_path(product),
    )
    return _ScoredRecommendation(score=Decimal(count), recommendation=recommendation)


def _build_ppp_gap_recommendation(
    product: PricingProductSnapshot,
) -> _ScoredRecommendation | None:
    by_code = {row.territory_code: row for row in product.prices}
    us_row = by_code.get("US")
    if (
        us_row is None
        or us_row.gdp_per_capita_usd is None
        or us_row.gdp_per_capita_usd <= 0
        or us_row.customer_price <= 0
    ):
        return None

    gaps: list[tuple[Decimal, PricingTerritorySnapshot]] = []
    for row in product.prices:
        if row.territory_code == "US" or row.currency_code == "USD":
            continue
        if row.gdp_per_capita_usd is None or row.gdp_per_capita_usd <= 0:
            continue

        suggested = (
            us_row.customer_price * row.gdp_per_capita_usd / us_row.gdp_per_capita_usd
        )
        if suggested <= 0:
            continue

        diff_ratio = abs(row.customer_price - suggested) / suggested
        if diff_ratio >= _PPP_GAP_THRESHOLD:
            gaps.append((diff_ratio, row))

    if len(gaps) < _MIN_PPP_GAP_TERRITORIES:
        return None

    gaps.sort(key=lambda item: item[0], reverse=True)
    strongest = gaps[:_MAX_FACT_TERRITORIES]
    strongest_fact = ", ".join(
        f"{row.territory_code} ({_percent(diff_ratio)} off)"
        for diff_ratio, row in strongest
    )

    recommendation = PricingRecommendation(
        id=f"pricing-ppp-gap-{product.kind}-{product.local_id}",
        category="pricing",
        priority="medium" if len(gaps) < 8 else "high",
        title=f"{product.name} is drifting from a PPP-style baseline",
        body=(
            f"{len(gaps)} cached territories are more than 35% away from a "
            "GDP-adjusted baseline anchored on the US price. A PPP preview is "
            "likely to surface obvious over- and under-priced markets."
        ),
        facts=[
            f"US anchor: {_format_price(us_row.customer_price, us_row.currency_code)}",
            f"Territories >35% off: {len(gaps)}",
            f"Largest gaps: {strongest_fact}",
        ],
        cta_label=_cta_label(product),
        cta_path=_cta_path(product),
    )
    score = Decimal(len(gaps)) + strongest[0][0]
    return _ScoredRecommendation(score=score, recommendation=recommendation)


def _build_sync_or_coverage_recommendation(
    products: list[PricingProductSnapshot],
) -> PricingRecommendation | None:
    if not products:
        return None

    best_product = max(products, key=lambda product: len(product.prices))
    if best_product.prices:
        if len(best_product.prices) >= _MIN_PRICE_ROWS_FOR_CONFIDENCE:
            return None
        return PricingRecommendation(
            id=f"pricing-coverage-{best_product.kind}-{best_product.local_id}",
            category="pricing",
            priority="low",
            title="Sync more territories before trusting pricing advice",
            body=(
                f"Only {len(best_product.prices)} cached storefronts are available "
                f"for {best_product.name}. A fuller sync will unlock stronger "
                "localization and PPP recommendations."
            ),
            facts=[
                f"Cached storefronts: {len(best_product.prices)}",
                f"Product: {best_product.product_id}",
            ],
            cta_label=_cta_label(best_product),
            cta_path=_cta_path(best_product),
        )

    return PricingRecommendation(
        id=f"pricing-sync-{best_product.kind}-{best_product.local_id}",
        category="pricing",
        priority="low",
        title="Sync pricing before looking for localization wins",
        body=(
            f"{best_product.name} has no cached territory prices yet, so the "
            "advisor cannot compare storefronts or spot PPP gaps."
        ),
        facts=[f"Product: {best_product.product_id}", "Cached storefronts: 0"],
        cta_label=_cta_label(best_product),
        cta_path=_cta_path(best_product),
    )


def _cta_label(product: PricingProductSnapshot) -> str:
    if product.kind == "subscription":
        return "Open subscription pricing"
    return "Open IAP pricing"


def _cta_path(product: PricingProductSnapshot) -> str:
    if product.kind == "subscription":
        params = {
            "group": str(product.group_id),
            "sub": str(product.local_id),
        }
    else:
        params = {
            "tab": "iap",
            "iap": str(product.local_id),
        }
    return f"/apps/{product.app_id}/pricing?{urlencode(params)}"


def _territory_list(rows: list[PricingTerritorySnapshot]) -> str:
    names = [row.territory_name for row in rows[:_MAX_FACT_TERRITORIES]]
    if len(rows) > _MAX_FACT_TERRITORIES:
        names.append(f"+{len(rows) - _MAX_FACT_TERRITORIES} more")
    return ", ".join(names)


def _format_price(price: Decimal, currency_code: str) -> str:
    normalized = price.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    return f"{normalized} {currency_code}"


def _percent(value: Decimal) -> str:
    pct = (value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{pct}%"


def _to_decimal(value: float | int | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
