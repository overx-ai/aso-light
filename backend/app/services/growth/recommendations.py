"""Growth recommendation rules.

The route layer owns authorization and database session lifetime; this module
loads read-only app signals and evaluates small composable rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.iap import IAPPrice, InAppPurchase
from app.models.subscription import (
    Subscription,
    SubscriptionGroup,
    SubscriptionPrice,
)
from app.models.territory import Territory
from app.schemas.growth import GrowthRecommendationOut, RecommendationEvidence

ProductKind = Literal["subscription", "iap"]


@dataclass(frozen=True)
class TerritoryPriceSnapshot:
    territory_code: str
    territory_name: str
    currency_code: str
    customer_price: Decimal
    gdp_per_capita_usd: Decimal | None


@dataclass(frozen=True)
class PricingProductSnapshot:
    kind: ProductKind
    id: int
    group_id: int | None
    name: str
    product_id: str
    prices: tuple[TerritoryPriceSnapshot, ...]

    def price_for_territory(self, territory_code: str) -> TerritoryPriceSnapshot | None:
        for price in self.prices:
            if price.territory_code == territory_code:
                return price
        return None


class PricingRecommendationRule(ABC):
    """Interface for pricing recommendation rules."""

    @abstractmethod
    def evaluate(
        self,
        *,
        app_id: int,
        product: PricingProductSnapshot,
    ) -> list[GrowthRecommendationOut]:
        """Return recommendations produced by this rule for one product."""

    def _pricing_path(self, app_id: int, product: PricingProductSnapshot) -> str:
        if product.kind == "subscription":
            group_query = f"group={product.group_id}&" if product.group_id else ""
            return f"/apps/{app_id}/pricing?{group_query}sub={product.id}"
        return f"/apps/{app_id}/pricing?tab=iap&iap={product.id}"

    def _product_label(self, product: PricingProductSnapshot) -> str:
        return f"{product.name} ({product.product_id})"


class MissingPricingCacheRule(PricingRecommendationRule):
    """Prompt users to sync current prices before optimization can run."""

    def evaluate(
        self,
        *,
        app_id: int,
        product: PricingProductSnapshot,
    ) -> list[GrowthRecommendationOut]:
        if product.prices:
            return []

        return [
            GrowthRecommendationOut(
                id=f"pricing-cache-missing-{product.kind}-{product.id}",
                category="pricing",
                severity="info",
                title=f"Sync prices for {product.name}",
                description=(
                    "No cached territory prices are available for this product."
                ),
                impact=(
                    "Growth Advisor cannot spot localization gaps until current "
                    "App Store prices are synced into the pricing workflow."
                ),
                cta_label="Open pricing sync",
                cta_path=self._pricing_path(app_id, product),
                evidence=[
                    RecommendationEvidence(
                        label="Product",
                        value=self._product_label(product),
                    ),
                ],
            )
        ]


class LocalizedUsdPricingRule(PricingRecommendationRule):
    """Flag lower-income USD territories that still match the US price.

    This intentionally compares only USD storefronts, avoiding cross-currency
    assumptions while still catching non-localized USD pricing patterns.
    """

    GDP_RATIO_THRESHOLD = Decimal("0.70")
    SAME_PRICE_TOLERANCE = Decimal("0.01")

    def evaluate(
        self,
        *,
        app_id: int,
        product: PricingProductSnapshot,
    ) -> list[GrowthRecommendationOut]:
        us_price = product.price_for_territory("US")
        if (
            us_price is None
            or us_price.currency_code != "USD"
            or us_price.gdp_per_capita_usd is None
        ):
            return []

        gdp_ceiling = us_price.gdp_per_capita_usd * self.GDP_RATIO_THRESHOLD
        matches = [
            price
            for price in product.prices
            if price.territory_code != "US"
            and price.currency_code == "USD"
            and price.gdp_per_capita_usd is not None
            and price.gdp_per_capita_usd <= gdp_ceiling
            and abs(price.customer_price - us_price.customer_price)
            <= self.SAME_PRICE_TOLERANCE
        ]
        if not matches:
            return []

        examples = sorted(
            matches,
            key=lambda price: price.gdp_per_capita_usd or Decimal("0"),
        )[:5]
        example_text = ", ".join(
            f"{p.territory_code} {self._money(p.customer_price, p.currency_code)}"
            for p in examples
        )

        return [
            GrowthRecommendationOut(
                id=f"pricing-usd-localization-{product.kind}-{product.id}",
                category="pricing",
                severity="warning",
                title=f"Localize USD prices for {product.name}",
                description=(
                    f"{len(matches)} lower-income USD storefronts match the US "
                    "customer price exactly."
                ),
                impact=(
                    "Equal USD pricing can depress conversion in lower-income "
                    "territories where a softer Apple price point may fit better."
                ),
                cta_label="Preview localized prices",
                cta_path=self._pricing_path(app_id, product),
                evidence=[
                    RecommendationEvidence(
                        label="US baseline",
                        value=(
                            f"{self._money(us_price.customer_price, 'USD')} "
                            f"at GDP/capita {self._gdp(us_price)}"
                        ),
                    ),
                    RecommendationEvidence(
                        label="Matching territories",
                        value=example_text,
                    ),
                ],
            )
        ]

    def _money(self, amount: Decimal, currency_code: str) -> str:
        value = amount.quantize(Decimal("0.01"))
        if currency_code == "USD":
            return f"${value}"
        return f"{value} {currency_code}"

    def _gdp(self, price: TerritoryPriceSnapshot) -> str:
        if price.gdp_per_capita_usd is None:
            return "unknown"
        return f"${price.gdp_per_capita_usd.quantize(Decimal('1'))}"


class GrowthRecommendationService:
    def __init__(
        self,
        session: AsyncSession,
        pricing_rules: tuple[PricingRecommendationRule, ...] | None = None,
    ) -> None:
        self.session = session
        self.pricing_rules = pricing_rules or (
            LocalizedUsdPricingRule(),
            MissingPricingCacheRule(),
        )

    async def recommendations_for_app(
        self,
        app_id: int,
    ) -> list[GrowthRecommendationOut]:
        pricing_products = await self._load_pricing_products(app_id)
        recommendations: list[GrowthRecommendationOut] = []
        for product in pricing_products:
            for rule in self.pricing_rules:
                recommendations.extend(rule.evaluate(app_id=app_id, product=product))

        return sorted(
            recommendations,
            key=lambda item: (
                {"critical": 0, "warning": 1, "info": 2}[item.severity],
                item.category,
                item.title,
            ),
        )

    async def _load_pricing_products(
        self,
        app_id: int,
    ) -> list[PricingProductSnapshot]:
        subscription_products = await self._load_subscription_products(app_id)
        iap_products = await self._load_iap_products(app_id)
        return [*subscription_products, *iap_products]

    async def _load_subscription_products(
        self,
        app_id: int,
    ) -> list[PricingProductSnapshot]:
        product_result = await self.session.execute(
            select(
                Subscription.id.label("id"),
                Subscription.group_id.label("group_id"),
                Subscription.name.label("name"),
                Subscription.product_id.label("product_id"),
            )
            .join(SubscriptionGroup, Subscription.group_id == SubscriptionGroup.id)
            .where(SubscriptionGroup.app_id == app_id)
        )
        products = {
            row.id: PricingProductSnapshot(
                kind="subscription",
                id=row.id,
                group_id=row.group_id,
                name=row.name,
                product_id=row.product_id,
                prices=(),
            )
            for row in product_result.all()
        }
        if not products:
            return []

        price_result = await self.session.execute(
            select(
                SubscriptionPrice.subscription_id.label("product_db_id"),
                Territory.code.label("territory_code"),
                Territory.name.label("territory_name"),
                Territory.currency_code.label("currency_code"),
                Territory.gdp_per_capita_usd.label("gdp_per_capita_usd"),
                SubscriptionPrice.customer_price.label("customer_price"),
            )
            .join(
                Subscription,
                Subscription.id == SubscriptionPrice.subscription_id,
            )
            .join(SubscriptionGroup, Subscription.group_id == SubscriptionGroup.id)
            .join(Territory, Territory.id == SubscriptionPrice.territory_id)
            .where(SubscriptionGroup.app_id == app_id)
        )
        prices_by_product: dict[int, list[TerritoryPriceSnapshot]] = {
            product_id: [] for product_id in products
        }
        for row in price_result.all():
            prices_by_product[row.product_db_id].append(
                TerritoryPriceSnapshot(
                    territory_code=row.territory_code,
                    territory_name=row.territory_name,
                    currency_code=row.currency_code,
                    customer_price=_to_decimal(row.customer_price),
                    gdp_per_capita_usd=_to_optional_decimal(row.gdp_per_capita_usd),
                )
            )

        return [
            replace(product, prices=tuple(prices_by_product[product.id]))
            for product in products.values()
        ]

    async def _load_iap_products(self, app_id: int) -> list[PricingProductSnapshot]:
        product_result = await self.session.execute(
            select(
                InAppPurchase.id.label("id"),
                InAppPurchase.name.label("name"),
                InAppPurchase.product_id.label("product_id"),
            ).where(InAppPurchase.app_id == app_id)
        )
        products = {
            row.id: PricingProductSnapshot(
                kind="iap",
                id=row.id,
                group_id=None,
                name=row.name,
                product_id=row.product_id,
                prices=(),
            )
            for row in product_result.all()
        }
        if not products:
            return []

        price_result = await self.session.execute(
            select(
                IAPPrice.iap_id.label("product_db_id"),
                Territory.code.label("territory_code"),
                Territory.name.label("territory_name"),
                Territory.currency_code.label("currency_code"),
                Territory.gdp_per_capita_usd.label("gdp_per_capita_usd"),
                IAPPrice.customer_price.label("customer_price"),
            )
            .join(InAppPurchase, InAppPurchase.id == IAPPrice.iap_id)
            .join(Territory, Territory.id == IAPPrice.territory_id)
            .where(InAppPurchase.app_id == app_id)
        )
        prices_by_product: dict[int, list[TerritoryPriceSnapshot]] = {
            product_id: [] for product_id in products
        }
        for row in price_result.all():
            prices_by_product[row.product_db_id].append(
                TerritoryPriceSnapshot(
                    territory_code=row.territory_code,
                    territory_name=row.territory_name,
                    currency_code=row.currency_code,
                    customer_price=_to_decimal(row.customer_price),
                    gdp_per_capita_usd=_to_optional_decimal(row.gdp_per_capita_usd),
                )
            )

        return [
            replace(product, prices=tuple(prices_by_product[product.id]))
            for product in products.values()
        ]


def _to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def _to_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _to_decimal(value)
