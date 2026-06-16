"""Schemas for pricing API endpoints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GDPTier = Literal["top", "mid", "low", "special"]
SubscriptionPeriod = Literal[
    "ONE_WEEK", "ONE_MONTH", "TWO_MONTHS", "THREE_MONTHS",
    "SIX_MONTHS", "ONE_YEAR",
]
# Introductory-offer durations include shorter periods (THREE_DAYS,
# TWO_WEEKS) that the regular subscriptionPeriod enum does not.
IntroOfferDuration = Literal[
    "THREE_DAYS", "ONE_WEEK", "TWO_WEEKS", "ONE_MONTH", "TWO_MONTHS",
    "THREE_MONTHS", "SIX_MONTHS", "ONE_YEAR",
]
IntroOfferMode = Literal["FREE_TRIAL", "PAY_AS_YOU_GO", "PAY_UP_FRONT"]


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


class SubscriptionAvailabilityResponse(BaseModel):
    """Alpha-2 territory codes where a subscription is currently available."""

    subscription_id: int
    territories: list[str]


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


class PricePreviewSkippedItem(BaseModel):
    """A territory excluded from a preview because no price could be computed.

    Distinguishes "could not compute" (e.g. ``missing_fx_rate``) from a
    territory that simply needs no change, so the UI can surface it.
    """

    territory_code: str
    territory_name: str
    reason: str


class PricePreviewResponse(BaseModel):
    """Full preview of suggested prices across all territories."""

    subscription_id: int
    subscription_name: str
    index_type: str
    base_price: float
    items: list[PricePreviewItem]
    skipped_territories: list[PricePreviewSkippedItem] = []


class PriceApplyItem(BaseModel):
    """A single territory price to apply."""

    territory_code: str
    price_point_id: str
    force: bool = False  # Bypass the ±50% safety band for this territory only.


class IntroOfferApplyConfig(BaseModel):
    """Worldwide free-trial config bundled with a price-apply request.

    Only ``FREE_TRIAL`` is supported here because PAY_AS_YOU_GO and
    PAY_UP_FRONT need a per-territory ``subscriptionPricePoint`` and
    therefore can't be expressed as a single worldwide offer.
    """

    duration: IntroOfferDuration
    number_of_periods: int = Field(default=1, ge=1, le=12)


class PriceApplyRequest(BaseModel):
    """Request body for applying prices to a subscription."""

    items: list[PriceApplyItem]
    intro_offer: IntroOfferApplyConfig | None = None


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
    intro_offer_synced: bool = False  # True if intro_offer config was applied
    intro_offer_failed: int = 0  # Per-territory intro-offer create/delete failures
    intro_offer_error: str | None = None


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
    skipped_territories: list[PricePreviewSkippedItem] = []


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


# ------------------------------------------------------------------
# Subscription group / subscription / intro offer write paths
# ------------------------------------------------------------------


class SubscriptionGroupCreate(BaseModel):
    """Create a new subscription group in ASC."""

    reference_name: str = Field(min_length=1, max_length=64)


class SubscriptionGroupUpdate(BaseModel):
    """Rename an existing subscription group."""

    reference_name: str = Field(min_length=1, max_length=64)


class GroupLocalizationCreate(BaseModel):
    """Create a subscriptionGroupLocalization for a given locale."""

    locale: str
    name: str = Field(min_length=1, max_length=30)
    custom_app_name: str | None = None


class GroupLocalizationUpdate(BaseModel):
    """Update a subscriptionGroupLocalization (locale is immutable)."""

    name: str = Field(min_length=1, max_length=30)
    custom_app_name: str | None = None


class GroupLocalizationResponse(BaseModel):
    """A single subscriptionGroupLocalization resource."""

    id: str
    locale: str
    name: str
    custom_app_name: str | None = None
    state: str | None = None


class SubscriptionCreate(BaseModel):
    """Create an auto-renewable subscription within a group."""

    product_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=64)
    period: SubscriptionPeriod
    family_sharable: bool = False
    available_in_all_territories: bool = True
    group_level: int = Field(default=1, ge=1, le=10)
    review_note: str | None = Field(default=None, max_length=4000)


class SubscriptionUpdate(BaseModel):
    """Update editable subscription metadata.

    ``productId`` and ``subscriptionPeriod`` are immutable in ASC and
    are intentionally not exposed here.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    group_level: int | None = Field(default=None, ge=1, le=10)
    family_sharable: bool | None = None
    review_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "SubscriptionUpdate":
        if all(
            getattr(self, f) is None
            for f in ("name", "group_level", "family_sharable", "review_note")
        ):
            raise ValueError("At least one field must be provided")
        return self


class IntroOfferCreate(BaseModel):
    """Create a subscription introductory offer.

    ``territory_code`` is alpha-2 (matching our DB convention); the
    route converts to alpha-3 before calling ASC. Apple **requires** a
    territory on every introductory offer — there is no worldwide
    option. To target every territory, use the price-apply route's
    bundled ``intro_offer`` config which loops through all priced
    territories.
    """

    territory_code: str
    offer_mode: IntroOfferMode
    duration: IntroOfferDuration
    number_of_periods: int = Field(ge=1, le=12)
    price_point_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("territory_code")
    @classmethod
    def _alpha2(cls, v: str) -> str:
        if not isinstance(v, str) or len(v) != 2 or not v.isalpha():
            raise ValueError(f"Territory code must be alpha-2: {v!r}")
        return v.upper()

    @model_validator(mode="after")
    def _offer_mode_constraints(self) -> "IntroOfferCreate":
        needs_price = self.offer_mode in {"PAY_AS_YOU_GO", "PAY_UP_FRONT"}
        if needs_price and not self.price_point_id:
            raise ValueError(
                f"price_point_id is required for offer_mode={self.offer_mode}"
            )
        if self.offer_mode == "FREE_TRIAL" and self.price_point_id:
            raise ValueError(
                "FREE_TRIAL offers must not include a price_point_id"
            )
        if (
            self.offer_mode in {"FREE_TRIAL", "PAY_UP_FRONT"}
            and self.number_of_periods != 1
        ):
            raise ValueError(
                f"number_of_periods must be 1 for offer_mode={self.offer_mode}"
            )
        return self


class IntroOfferResponse(BaseModel):
    """A subscriptionIntroductoryOffer resource (alpha-2 territory code).

    ``territory_code`` is ``None`` only when our reverse alpha-3 → alpha-2
    lookup fails on a territory Apple has but we don't track. Every
    Apple intro offer is tied to a territory.
    """

    id: str
    territory_code: str | None
    offer_mode: IntroOfferMode
    duration: IntroOfferDuration
    number_of_periods: int
    price_point_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
