"""Schemas for pricing API endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GDPTier = Literal["top", "mid", "low", "special"]


class SubscriptionGroupResponse(BaseModel):
    """A subscription group with its subscriptions."""

    id: int
    asc_group_id: str
    name: str
    app_id: int

    model_config = ConfigDict(from_attributes=True)


class SubscriptionResponse(BaseModel):
    """A single auto-renewable subscription."""

    id: int
    asc_subscription_id: str
    name: str
    product_id: str
    group_id: int

    model_config = ConfigDict(from_attributes=True)


class SubscriptionGroupWithSubscriptionsResponse(BaseModel):
    """A subscription group with nested subscriptions."""

    id: int
    asc_group_id: str
    name: str
    app_id: int
    subscriptions: list[SubscriptionResponse]

    model_config = ConfigDict(from_attributes=True)


class PricePointResponse(BaseModel):
    """A single price point for a territory."""

    territory_code: str
    territory_name: str
    currency_code: str
    customer_price: float
    proceeds: float
    price_point_id: str | None = None
    vat_rate: float = 0.0


class SubscriptionPricesResponse(BaseModel):
    """All current prices for a subscription."""

    subscription_id: int
    subscription_name: str
    product_id: str
    prices: list[PricePointResponse]


class GDPBracketConfig(BaseModel):
    """Configuration for the GDP-bracket pricing strategy."""

    tier_prices_usd: dict[GDPTier, Decimal] = Field(
        ...,
        description="Absolute USD price per tier; all four tiers required.",
    )
    tier_thresholds_usd: dict[Literal["top_min", "mid_min"], Decimal] = Field(
        ...,
        description="GDP/capita PPP cutoffs (USD) for top and mid tiers.",
    )
    manual_overrides: dict[str, GDPTier] = Field(default_factory=dict)
    special_territories: list[str] = Field(default_factory=list)

    @field_validator("tier_prices_usd")
    @classmethod
    def _all_tiers_present(cls, v: dict[GDPTier, Decimal]) -> dict[GDPTier, Decimal]:
        missing = {"top", "mid", "low", "special"} - v.keys()
        if missing:
            raise ValueError(f"Missing tier prices: {sorted(missing)}")
        for tier, price in v.items():
            if price <= 0:
                raise ValueError(f"Tier {tier} price must be > 0, got {price}")
        return v

    @field_validator("manual_overrides", "special_territories")
    @classmethod
    def _alpha2_codes(cls, v):
        """Validate and normalize alpha-2 territory codes to uppercase.

        Territory codes in the DB are stored uppercase (e.g. "US", "GB"). Any
        casing is accepted from clients but we normalize to upper here so that
        downstream lookups in ``assign_tier`` always match.
        """
        def _check(code: str) -> str:
            if not isinstance(code, str) or len(code) != 2 or not code.isalpha():
                raise ValueError(
                    f"Territory code must be 2-letter alpha-2: {code!r}"
                )
            return code.upper()

        if isinstance(v, dict):
            return {_check(k): val for k, val in v.items()}
        return [_check(code) for code in v]

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "GDPBracketConfig":
        top_min = self.tier_thresholds_usd.get("top_min")
        mid_min = self.tier_thresholds_usd.get("mid_min")
        if top_min is None or mid_min is None:
            raise ValueError("Both top_min and mid_min thresholds required")
        if top_min <= mid_min:
            raise ValueError(
                f"top_min ({top_min}) must be greater than mid_min ({mid_min})"
            )
        return self


class PricePreviewRequest(BaseModel):
    """Request body for price preview calculation."""

    index_type: Literal[
        "exchange_rate", "ppp", "bigmac", "netflix", "spotify", "fixed_payout",
        "gdp_brackets",
    ]
    base_price: float = 0.0
    base_territory_code: str = "US"
    apply_vat: bool = False
    charming_mode: Literal["none", ".99", "99", ".95", "95", "smart"] = "none"
    gdp_config: GDPBracketConfig | None = None

    @model_validator(mode="after")
    def _gdp_config_required_for_brackets(self) -> "PricePreviewRequest":
        if self.index_type == "gdp_brackets" and self.gdp_config is None:
            raise ValueError(
                "gdp_config is required when index_type='gdp_brackets'"
            )
        return self


class PricePreviewItem(BaseModel):
    """A single suggested price for a territory."""

    territory_code: str
    territory_name: str
    currency_code: str
    current_price: float | None
    suggested_price: float
    nearest_apple_price: float | None = None
    price_point_id: str | None = None
    diff_percent: float | None = None
    would_be_skipped: bool = False


class PricePreviewResponse(BaseModel):
    """Full preview of suggested prices across all territories."""

    subscription_id: int
    subscription_name: str
    index_type: str
    base_price: float
    items: list[PricePreviewItem]


class PriceApplyItem(BaseModel):
    """A single territory price to apply."""

    territory_code: str
    price_point_id: str
    force: bool = False  # Bypass the ±50% safety band for this territory only.


class PriceApplyRequest(BaseModel):
    """Request body for applying prices to a subscription."""

    items: list[PriceApplyItem]


class PriceApplySkippedItem(BaseModel):
    """A territory skipped during price apply due to safety limits."""

    territory_code: str
    reason: str
    current_price: float
    new_price: float
    diff_percent: float


class PriceApplyResponse(BaseModel):
    """Result of applying prices."""

    applied: int
    failed: int
    skipped: int = 0
    errors: list[str] = []
    skipped_items: list[PriceApplySkippedItem] = []


class SyncPricesResponse(BaseModel):
    """Result of syncing prices from ASC."""

    prices_synced: int
    price_points_synced: int


class PricePointSyncResponse(BaseModel):
    """Result of syncing price points to filesystem cache."""

    territories_synced: int
    price_points_total: int


class PricePointCacheStatus(BaseModel):
    """Status of the price point filesystem cache."""

    cached_territories: int
    synced_at: str | None = None


class IAPResponse(BaseModel):
    """An in-app purchase."""

    id: int
    asc_iap_id: str
    name: str
    product_id: str
    iap_type: str
    app_id: int

    model_config = ConfigDict(from_attributes=True)


class IAPPricePointResponse(BaseModel):
    """A single IAP price for a territory."""

    territory_code: str
    territory_name: str
    currency_code: str
    customer_price: float
    proceeds: float
    price_point_id: str | None = None


class IAPPricesResponse(BaseModel):
    """All current prices for an IAP."""

    iap_id: int
    iap_name: str
    product_id: str
    prices: list[IAPPricePointResponse]


class IAPPricePreviewResponse(BaseModel):
    """Full preview of suggested prices for an IAP across all territories."""

    iap_id: int
    iap_name: str
    index_type: str
    base_price: float
    items: list[PricePreviewItem]


# ------------------------------------------------------------------
# Localization schemas
# ------------------------------------------------------------------


class LocalizationCreate(BaseModel):
    """Request body for creating a single localization."""

    locale: str
    name: str
    description: str


class LocalizationUpdate(BaseModel):
    """Request body for updating a single localization (locale is immutable)."""

    name: str
    description: str


class LocalizationResponse(BaseModel):
    """A single localization entry."""

    id: str
    locale: str
    name: str
    description: str


class BulkLocalizationRequest(BaseModel):
    """Request body for bulk create/update of localizations."""

    localizations: list[LocalizationCreate]


class BulkLocalizationResponse(BaseModel):
    """Result of a bulk localization sync."""

    created: int
    updated: int
    localizations: list[LocalizationResponse]


class PriceResolveRequest(BaseModel):
    """Resolve a manual price to the nearest Apple price tier."""

    territory_code: str
    price: float


class PriceResolveResponse(BaseModel):
    """Resolved Apple price tier for a manual price."""

    territory_code: str
    currency_code: str
    customer_price: float
    proceeds: float
    price_point_id: str


class ReviewScreenshotResponse(BaseModel):
    """Review screenshot metadata."""

    id: str
    file_name: str
    file_size: int
    image_url: str | None = None
