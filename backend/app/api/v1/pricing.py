"""Pricing API endpoints for subscriptions and in-app purchases."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.session import get_session
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.economic_index import EconomicIndex
from app.models.iap import IAPPrice, InAppPurchase
from app.models.subscription import (
    Subscription,
    SubscriptionGroup,
    SubscriptionPrice,
)
from app.models.territory import Territory
from app.schemas.pricing import (
    BulkLocalizationRequest,
    BulkLocalizationResponse,
    IAPPricePointResponse,
    IAPPricePreviewResponse,
    IAPPricesResponse,
    IAPResponse,
    LocalizationCreate,
    LocalizationResponse,
    LocalizationUpdate,
    PriceApplyRequest,
    PriceApplyResponse,
    PriceApplySkippedItem,
    PricePointCacheStatus,
    PricePointResponse,
    PricePointSyncResponse,
    PricePreviewItem,
    PricePreviewRequest,
    PricePreviewResponse,
    PriceResolveRequest,
    PriceResolveResponse,
    ReviewScreenshotResponse,
    SubscriptionGroupWithSubscriptionsResponse,
    SubscriptionPricesResponse,
    SyncPricesResponse,
)
from app.data.territories import ALPHA2_TO_ALPHA3
from app.services.asc.client import ASCClient
from app.services.asc.errors import ASCAPIError
from app.services.asc.price_point_cache import PricePointCache
from app.services.asc.pricing import ASCPricingService
from app.services.pricing.currency import effective_currency

logger = logging.getLogger(__name__)
router = APIRouter()

# Safety band: skip territories where the new price differs from the
# current price by more than this fraction in either direction.
SAFETY_BAND_PCT = 0.50
SAFETY_MAX_UP = 1.0 + SAFETY_BAND_PCT
SAFETY_MAX_DOWN = 1.0 - SAFETY_BAND_PCT
SAFETY_LABEL = f"±{int(SAFETY_BAND_PCT * 100)}%"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _get_verified_app(
    app_id: int,
    user_id: int,
    session: AsyncSession,
) -> App:
    """Load an App record and verify that it belongs to the current user.

    Raises HTTPException 404/403 on failure.
    """
    result = await session.execute(select(App).where(App.id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found",
        )

    cred_result = await session.execute(
        select(ASCCredential.id).where(
            ASCCredential.id == app.credential_id,
            ASCCredential.user_id == user_id,
        )
    )
    if cred_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this app",
        )

    return app


async def _get_asc_client_for_app(
    app: App,
    session: AsyncSession,
) -> ASCClient:
    """Build an ASCClient from the credential that owns the given app."""
    result = await session.execute(
        select(ASCCredential).where(ASCCredential.id == app.credential_id)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Credential for app not found",
        )
    return ASCClient.from_credential(credential)


async def _get_verified_subscription(
    subscription_id: int, app_id: int, session: AsyncSession
) -> Subscription:
    """Load a subscription and verify it belongs to the given app."""
    sub_result = await session.execute(
        select(Subscription)
        .join(SubscriptionGroup)
        .where(
            Subscription.id == subscription_id,
            SubscriptionGroup.app_id == app_id,
        )
    )
    subscription = sub_result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found for this app",
        )
    return subscription


async def _get_verified_iap(
    iap_id: int, app_id: int, session: AsyncSession
) -> InAppPurchase:
    """Load an IAP and verify it belongs to the given app."""
    iap_result = await session.execute(
        select(InAppPurchase).where(
            InAppPurchase.id == iap_id,
            InAppPurchase.app_id == app_id,
        )
    )
    iap = iap_result.scalar_one_or_none()
    if iap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="In-app purchase not found for this app",
        )
    return iap


async def _get_territory_map(session: AsyncSession) -> dict[str, Territory]:
    """Return a dict mapping territory code -> Territory ORM object.

    Supports both ISO alpha-2 (our DB) and alpha-3 (ASC API) lookups.

    NOTE: Because the same Territory appears under both alpha-2 and
    alpha-3 keys, callers that need to iterate unique territories
    should use ``_unique_territories(territory_map)`` instead of
    ``territory_map.values()``.
    """
    result = await session.execute(select(Territory))
    territories = result.scalars().all()
    tmap: dict[str, Territory] = {}
    for t in territories:
        tmap[t.code] = t  # alpha-2: "US", "GB", etc.
        # Also map alpha-3 ASC territory IDs
        alpha3 = ALPHA2_TO_ALPHA3.get(t.code)
        if alpha3:
            tmap[alpha3] = t
    return tmap


def _unique_territories(territory_map: dict[str, Territory]) -> list[Territory]:
    """Return deduplicated territories from the lookup map.

    The territory map contains both alpha-2 and alpha-3 keys pointing
    to the same Territory objects.  Iterating .values() directly would
    yield duplicates.
    """
    seen: set[int] = set()
    result: list[Territory] = []
    for t in territory_map.values():
        if t.id not in seen:
            seen.add(t.id)
            result.append(t)
    return result



# ------------------------------------------------------------------
# Subscription endpoints
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/subscriptions",
    response_model=list[SubscriptionGroupWithSubscriptionsResponse],
)
async def list_subscriptions(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SubscriptionGroupWithSubscriptionsResponse]:
    """Fetch subscription groups and subscriptions from ASC, sync to DB."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)

        # Fetch groups from ASC
        groups_data = await pricing_service.list_subscription_groups(app.asc_app_id)

        synced_groups: list[SubscriptionGroup] = []

        for group_data in groups_data:
            asc_group_id = group_data["id"]
            group_name = group_data.get("attributes", {}).get(
                "referenceName", "Unknown"
            )

            # Upsert subscription group
            existing_group_result = await session.execute(
                select(SubscriptionGroup).where(
                    SubscriptionGroup.app_id == app.id,
                    SubscriptionGroup.asc_group_id == asc_group_id,
                )
            )
            group_record = existing_group_result.scalar_one_or_none()

            if group_record:
                group_record.name = group_name
            else:
                group_record = SubscriptionGroup(
                    app_id=app.id,
                    asc_group_id=asc_group_id,
                    name=group_name,
                )
                session.add(group_record)

            await session.flush()

            # Fetch subscriptions for this group
            subs_data = await pricing_service.list_subscriptions(asc_group_id)

            for sub_data in subs_data:
                asc_sub_id = sub_data["id"]
                attrs = sub_data.get("attributes", {})

                existing_sub_result = await session.execute(
                    select(Subscription).where(
                        Subscription.group_id == group_record.id,
                        Subscription.asc_subscription_id == asc_sub_id,
                    )
                )
                sub_record = existing_sub_result.scalar_one_or_none()

                if sub_record:
                    sub_record.name = attrs.get("name", sub_record.name)
                    sub_record.product_id = attrs.get(
                        "productId", sub_record.product_id
                    )
                else:
                    sub_record = Subscription(
                        group_id=group_record.id,
                        asc_subscription_id=asc_sub_id,
                        name=attrs.get("name", "Unknown"),
                        product_id=attrs.get("productId", ""),
                    )
                    session.add(sub_record)

            await session.flush()
            synced_groups.append(group_record)

    # Reload groups with subscriptions for response
    result = await session.execute(
        select(SubscriptionGroup)
        .options(selectinload(SubscriptionGroup.subscriptions))
        .where(SubscriptionGroup.app_id == app.id)
    )
    groups = result.scalars().all()

    return [
        SubscriptionGroupWithSubscriptionsResponse.model_validate(g)
        for g in groups
    ]


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/prices",
    response_model=SubscriptionPricesResponse,
)
async def get_subscription_prices(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionPricesResponse:
    """Return cached prices for a subscription from DB (no ASC calls)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    subscription = await _get_verified_subscription(subscription_id, app.id, session)
    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    cached_result = await session.execute(
        select(SubscriptionPrice).where(
            SubscriptionPrice.subscription_id == subscription.id
        )
    )
    cached_prices = cached_result.scalars().all()

    pp_cache = PricePointCache(app.asc_app_id)
    price_responses: list[PricePointResponse] = []
    for p in cached_prices:
        territory = territory_by_id.get(p.territory_id)
        if territory is None:
            continue
        cached_tiers = await pp_cache.get(territory.code)
        currency = effective_currency(territory, cached_tiers)
        price_responses.append(
            PricePointResponse(
                territory_code=territory.code,
                territory_name=territory.name,
                currency_code=currency,
                customer_price=p.customer_price,
                proceeds=p.proceeds,
                price_point_id=p.price_point_id,
                vat_rate=territory.vat_rate,
            )
        )

    return SubscriptionPricesResponse(
        subscription_id=subscription.id,
        subscription_name=subscription.name,
        product_id=subscription.product_id,
        prices=price_responses,
    )


# ------------------------------------------------------------------
# Sync from Apple
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/sync",
    response_model=SyncPricesResponse,
)
async def sync_subscription_prices(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SyncPricesResponse:
    """Sync current prices from ASC into DB cache.

    Fetches the ~175 current prices (one per territory) — takes ~2 seconds.
    Price points (all available tiers) are NOT fetched here — they're looked
    up per-territory on demand during apply.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)
    territory_map = await _get_territory_map(session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)

        prices_data = await pricing_service.get_subscription_prices(
            subscription.asc_subscription_id
        )
        now = datetime.now(timezone.utc)
        prices_synced = 0

        # Pre-load all existing prices in one query to avoid N+1
        existing_result = await session.execute(
            select(SubscriptionPrice).where(
                SubscriptionPrice.subscription_id == subscription.id
            )
        )
        existing_by_territory: dict[int, SubscriptionPrice] = {
            p.territory_id: p for p in existing_result.scalars().all()
        }

        for price_item in prices_data:
            territory_code = price_item.get("territory_code", "")
            territory = territory_map.get(territory_code)
            if territory is None:
                continue

            price_record = existing_by_territory.get(territory.id)

            if price_record:
                price_record.customer_price = price_item.get("customer_price", 0.0)
                price_record.proceeds = price_item.get("proceeds", 0.0)
                price_record.price_point_id = price_item.get("price_point_id")
                price_record.synced_at = now
            else:
                price_record = SubscriptionPrice(
                    subscription_id=subscription.id,
                    territory_id=territory.id,
                    customer_price=price_item.get("customer_price", 0.0),
                    proceeds=price_item.get("proceeds", 0.0),
                    price_point_id=price_item.get("price_point_id"),
                    synced_at=now,
                )
                session.add(price_record)
            prices_synced += 1

        await session.flush()

    logger.info("Sync complete: %d prices for subscription %s", prices_synced, subscription_id)
    return SyncPricesResponse(
        prices_synced=prices_synced,
        price_points_synced=0,
    )


# ------------------------------------------------------------------
# Sync Price Points (filesystem cache)
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/price-points/sync",
    response_model=PricePointSyncResponse,
)
async def sync_price_points(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PricePointSyncResponse:
    """Sync Apple price tiers to the app-wide filesystem cache.

    Always syncs every seeded territory — Apple's tier ladder is global
    per (app, product_type), independent of which territories the
    subscription currently has prices in. The wider sync makes the
    Preview pane workable for territories you haven't priced yet.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)
    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    territory_codes = sorted({t.code for t in territory_by_id.values()})

    # App-wide tier cache: every subscription on this app shares the
    # same ladder, so syncing once via any sub populates the cache for
    # all of them. We pass this sub's asc_id to the API caller below.
    # Don't clear() — fetch_and_cache_all skips already-cached entries
    # so a retry only re-fetches the ones that previously failed.
    cache = PricePointCache(app.asc_app_id)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        total_points = await cache.fetch_and_cache_all(
            territory_codes,
            subscription.asc_subscription_id,
            pricing_service,
        )

    logger.info(
        "Price tiers sync: %d territories, %d tiers for app %s (via sub %s)",
        len(territory_codes), total_points, app.asc_app_id, subscription_id,
    )
    return PricePointSyncResponse(
        territories_synced=len(territory_codes),
        price_points_total=total_points,
    )


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/price-points/status",
    response_model=PricePointCacheStatus,
)
async def get_price_point_cache_status(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PricePointCacheStatus:
    """Return the status of the price point filesystem cache."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    await _get_verified_subscription(subscription_id, app.id, session)

    cache = PricePointCache(app.asc_app_id)
    info = await cache.status()
    return PricePointCacheStatus(**info)


# ------------------------------------------------------------------
# Price Preview
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/prices/preview",
    response_model=PricePreviewResponse,
)
async def preview_subscription_prices(
    app_id: int,
    subscription_id: int,
    body: PricePreviewRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PricePreviewResponse:
    """Preview suggested prices based on economic index data.

    Calculates suggested prices for each territory using the formula:
        suggested = base_price * (territory_index / base_index)

    Then optionally matches each suggested price to the nearest Apple
    price point available for the subscription.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)
    territory_map = await _get_territory_map(session)

    # Load current prices from DB
    current_prices_result = await session.execute(
        select(SubscriptionPrice).where(
            SubscriptionPrice.subscription_id == subscription.id
        )
    )
    current_prices = current_prices_result.scalars().all()
    current_price_by_territory: dict[int, SubscriptionPrice] = {
        p.territory_id: p for p in current_prices
    }

    # Load cached tier ladder (app-wide) and enrich with this sub's
    # computed price_point_ids so downstream nearest-match works.
    cache = PricePointCache(app.asc_app_id)
    price_points_by_territory: dict[str, list[dict]] = {}
    all_territories = _unique_territories(territory_map)
    for territory in all_territories:
        cached = await cache.get_with_price_point_ids(
            territory.code, subscription.asc_subscription_id,
        )
        if cached is not None:
            price_points_by_territory[territory.code] = cached

    preview_items: list[PricePreviewItem] = []

    if body.index_type == "exchange_rate":
        # --- Exchange rate branch: use live FX rates ---
        from decimal import Decimal

        from app.core.config import settings
        from app.services.pricing.currency_rounding import apply_currency_rounding
        from app.services.pricing.vat import apply_vat
        from app.services.rates import RateCacheClient, RateCacheError

        # Determine base currency from base territory
        base_territory = territory_map.get(body.base_territory_code)
        if base_territory is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base territory '{body.base_territory_code}' not found",
            )
        base_currency = effective_currency(
            base_territory, price_points_by_territory.get(base_territory.code),
        )

        try:
            rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
            rates = await rate_client.get_rates(base=base_currency)
        except RateCacheError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch exchange rates: {exc}",
            )

        for territory in all_territories:
            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            # Rate for the base currency itself is 1.0
            if currency == base_currency:
                rate = 1.0
            else:
                rate = rates.get(currency)
                if rate is None:
                    continue

            suggested_decimal = Decimal(str(body.base_price)) * Decimal(str(rate))

            # Apply VAT if requested
            if body.apply_vat and territory.vat_rate and territory.vat_rate > 0:
                suggested_decimal = apply_vat(
                    suggested_decimal, territory.vat_rate
                )

            # Apply smart currency rounding or charming
            if body.charming_mode == "smart":
                suggested_decimal = apply_currency_rounding(
                    suggested_decimal, currency
                )
                suggested = float(suggested_decimal)
            else:
                suggested = _apply_charming(
                    float(suggested_decimal), body.charming_mode, currency
                )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))
    elif body.index_type == "gdp_brackets":
        # --- GDP-bracket branch: tier per territory, absolute USD per tier ---
        from decimal import Decimal

        from app.core.config import settings
        from app.services.pricing.currency_rounding import apply_currency_rounding
        from app.services.pricing.gdp_brackets import assign_tier
        from app.services.pricing.vat import apply_vat
        from app.services.rates import RateCacheClient, RateCacheError

        assert body.gdp_config is not None  # validator enforced

        gdp_indices_result = await session.execute(
            select(EconomicIndex).where(
                EconomicIndex.index_type == "gdp_per_capita_ppp"
            )
        )
        gdp_by_territory_id: dict[int, float] = {
            idx.territory_id: idx.value for idx in gdp_indices_result.scalars().all()
        }

        try:
            rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
            rates = await rate_client.get_rates(base="USD")
        except RateCacheError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch exchange rates: {exc}",
            )

        for territory in all_territories:
            tier = assign_tier(
                territory.code,
                gdp_by_territory_id.get(territory.id),
                body.gdp_config,
            )
            tier_price_usd = body.gdp_config.tier_prices_usd[tier]

            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            if currency == "USD":
                rate = Decimal("1")
            else:
                rate_value = rates.get(currency)
                if rate_value is None:
                    continue
                rate = Decimal(str(rate_value))

            suggested_decimal = tier_price_usd * rate

            if body.apply_vat and territory.vat_rate and territory.vat_rate > 0:
                suggested_decimal = apply_vat(
                    suggested_decimal, territory.vat_rate
                )

            if body.charming_mode == "smart":
                suggested_decimal = apply_currency_rounding(
                    suggested_decimal, currency
                )
                suggested = float(suggested_decimal)
            else:
                suggested = _apply_charming(
                    float(suggested_decimal), body.charming_mode, currency
                )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))
    else:
        # --- Economic index branch (ppp, bigmac, netflix, etc.) ---
        # Load economic indices for the requested type
        indices_result = await session.execute(
            select(EconomicIndex).where(
                EconomicIndex.index_type == body.index_type
            )
        )
        indices = indices_result.scalars().all()

        # Build territory_id -> index value map
        index_by_territory: dict[int, float] = {
            idx.territory_id: idx.value for idx in indices
        }

        # Find base territory index value
        base_territory = territory_map.get(body.base_territory_code)
        if base_territory is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base territory '{body.base_territory_code}' not found",
            )
        base_index_value = index_by_territory.get(base_territory.id)
        if base_index_value is None or base_index_value == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"No {body.index_type} index data for territory "
                    f"'{body.base_territory_code}'"
                ),
            )

        for territory in all_territories:
            territory_index = index_by_territory.get(territory.id)
            if territory_index is None:
                continue

            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            suggested = body.base_price * (territory_index / base_index_value)

            # Apply charming price
            suggested = _apply_charming(
                suggested, body.charming_mode, currency,
            )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))

    return PricePreviewResponse(
        subscription_id=subscription.id,
        subscription_name=subscription.name,
        index_type=body.index_type,
        base_price=body.base_price,
        items=preview_items,
    )


def _build_preview_item(
    *,
    territory: Territory,
    currency_code: str,
    suggested: float,
    current_price_by_territory: dict[int, Any],
    price_points_by_territory: dict[str, list[dict]],
) -> PricePreviewItem:
    """Build a PricePreviewItem with nearest price point and safety flag.

    Shared by subscription and IAP preview branches.  The values in
    *current_price_by_territory* must have a ``customer_price`` attribute
    (works with both SubscriptionPrice and IAPPrice).
    """
    current = current_price_by_territory.get(territory.id)
    current_price = current.customer_price if current else None

    nearest_price: float | None = None
    nearest_pp_id: str | None = None
    territory_pps = price_points_by_territory.get(territory.code, [])
    if territory_pps:
        nearest = min(
            territory_pps,
            key=lambda pp: abs(pp["customer_price"] - suggested),
        )
        nearest_price = nearest["customer_price"]
        nearest_pp_id = nearest["price_point_id"]

    # Use nearest Apple price if available, otherwise fall back to suggested
    compare_price = nearest_price if nearest_price is not None else suggested

    diff_percent: float | None = None
    if current_price and current_price > 0:
        diff_percent = round(
            ((compare_price - current_price) / current_price) * 100, 2
        )

    would_be_skipped = (
        current_price is not None
        and current_price > 0
        and (
            compare_price > current_price * SAFETY_MAX_UP
            or compare_price < current_price * SAFETY_MAX_DOWN
        )
    )

    return PricePreviewItem(
        territory_code=territory.code,
        territory_name=territory.name,
        currency_code=currency_code,
        current_price=current_price,
        suggested_price=round(suggested, 2),
        nearest_apple_price=nearest_price,
        price_point_id=nearest_pp_id,
        diff_percent=diff_percent,
        would_be_skipped=would_be_skipped,
    )


def _apply_charming(price: float, mode: str, currency_code: str = "USD") -> float:
    """Apply charming price adjustment.

    Args:
        price: Raw calculated price.
        mode: One of "none", "99", "95", "smart".
        currency_code: ISO 4217 currency code (used for "smart" mode).

    Returns:
        Adjusted price.
    """
    if mode == "smart":
        from decimal import Decimal

        from app.services.pricing.currency_rounding import apply_currency_rounding

        return float(apply_currency_rounding(Decimal(str(price)), currency_code))
    if mode in ("99", ".99"):
        return float(int(price)) + 0.99 if price > 1 else price
    if mode in ("95", ".95"):
        return float(int(price)) + 0.95 if price > 1 else price
    return price


# ------------------------------------------------------------------
# Resolve Manual Price
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/prices/resolve",
    response_model=PriceResolveResponse,
)
async def resolve_manual_price(
    app_id: int,
    subscription_id: int,
    body: PriceResolveRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PriceResolveResponse:
    """Resolve a manual price to the nearest Apple price tier.

    Reads from the filesystem price point cache — no ASC API calls.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)

    cache = PricePointCache(app.asc_app_id)
    pps = await cache.get_with_price_point_ids(
        body.territory_code, subscription.asc_subscription_id,
    )
    if not pps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No cached price tiers for territory '{body.territory_code}'. "
                f"Sync price tiers first."
            ),
        )

    nearest = min(pps, key=lambda pp: abs(pp["customer_price"] - body.price))
    return PriceResolveResponse(
        territory_code=body.territory_code,
        currency_code=nearest["currency_code"],
        customer_price=nearest["customer_price"],
        proceeds=nearest["proceeds"],
        price_point_id=nearest["price_point_id"],
    )


# ------------------------------------------------------------------
# Apply Prices
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/prices/apply",
    response_model=PriceApplyResponse,
)
async def apply_subscription_prices(
    app_id: int,
    subscription_id: int,
    body: PriceApplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PriceApplyResponse:
    """Apply selected price points to a subscription via ASC API.

    Includes a ±50% safety check per territory: if the new price
    differs from the current Apple price by more than 50% in either
    direction, the territory is skipped. Set ``force=True`` on a
    specific item to bypass the check for that territory only —
    useful when an unusually low initial price legitimately needs a
    large adjustment.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)

    # Load current prices from DB for the ±50% safety check
    current_prices_result = await session.execute(
        select(SubscriptionPrice).where(
            SubscriptionPrice.subscription_id == subscription.id
        )
    )
    current_prices = current_prices_result.scalars().all()
    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    current_price_by_code: dict[str, float] = {}
    for p in current_prices:
        territory = territory_by_id.get(p.territory_id)
        if territory:
            current_price_by_code[territory.code] = p.customer_price

    cache = PricePointCache(app.asc_app_id)

    applied = 0
    failed = 0
    skipped = 0
    errors: list[str] = []
    skipped_items: list[PriceApplySkippedItem] = []

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)

        for item in body.items:
            tc = item.territory_code
            current_price = current_price_by_code.get(tc)

            # Ensure tier ladder is cached (on-demand fetch if missing).
            territory_pps = await cache.get_with_price_point_ids(
                tc, subscription.asc_subscription_id,
            )
            if territory_pps is None:
                try:
                    await cache.fetch_and_cache(
                        tc, subscription.asc_subscription_id,
                        pricing_service,
                    )
                    territory_pps = await cache.get_with_price_point_ids(
                        tc, subscription.asc_subscription_id,
                    ) or []
                except Exception:
                    logger.warning(
                        "Failed to fetch price tiers for %s", tc,
                        exc_info=True,
                    )
                    failed += 1
                    errors.append(
                        f"Territory {tc}: failed to fetch price tiers"
                    )
                    continue

            # Look up the price for the chosen price_point_id
            new_price: float | None = None
            for pp in territory_pps:
                if pp["price_point_id"] == item.price_point_id:
                    new_price = pp["customer_price"]
                    break

            # Reject unknown price_point_ids -- they bypass the safety check
            if new_price is None:
                failed += 1
                errors.append(
                    f"Territory {tc}: price_point_id "
                    f"{item.price_point_id!r} not found in cached tiers"
                )
                continue

            # Safety check: skip if change exceeds ±50% (unless item.force)
            if (
                not item.force
                and new_price is not None
                and current_price is not None
                and current_price > 0
                and (
                    new_price > current_price * SAFETY_MAX_UP
                    or new_price < current_price * SAFETY_MAX_DOWN
                )
            ):
                diff_pct = round(
                    ((new_price - current_price) / current_price) * 100, 2
                )
                skipped += 1
                skipped_items.append(
                    PriceApplySkippedItem(
                        territory_code=tc,
                        reason=(
                            f"Price change {diff_pct:+}% exceeds "
                            f"safety limit ({SAFETY_LABEL})"
                        ),
                        current_price=current_price,
                        new_price=new_price,
                        diff_percent=diff_pct,
                    )
                )
                logger.info(
                    "Skipped %s: +%.1f%% (%.2f → %.2f)",
                    tc, diff_pct, current_price, new_price,
                )
                continue

            try:
                await pricing_service.create_subscription_price(
                    subscription_id=subscription.asc_subscription_id,
                    price_point_id=item.price_point_id,
                )
                applied += 1
            except ASCAPIError as exc:
                failed += 1
                errors.append(
                    f"Territory {tc}: {exc.message}"
                )
                logger.warning(
                    "Failed to apply price for subscription %s, "
                    "territory %s: %s",
                    subscription_id, tc, exc.message,
                )

    return PriceApplyResponse(
        applied=applied,
        failed=failed,
        skipped=skipped,
        errors=errors,
        skipped_items=skipped_items,
    )


# ------------------------------------------------------------------
# IAP endpoints
# ------------------------------------------------------------------


@router.get("/{app_id}/iaps", response_model=list[IAPResponse])
async def list_iaps(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[IAPResponse]:
    """Fetch in-app purchases from ASC and sync to DB."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        iaps_data = await pricing_service.list_iaps(app.asc_app_id)

    for iap_data in iaps_data:
        asc_iap_id = iap_data["id"]
        attrs = iap_data.get("attributes", {})

        existing_result = await session.execute(
            select(InAppPurchase).where(
                InAppPurchase.app_id == app.id,
                InAppPurchase.asc_iap_id == asc_iap_id,
            )
        )
        iap_record = existing_result.scalar_one_or_none()

        if iap_record:
            iap_record.name = attrs.get("name", iap_record.name)
            iap_record.product_id = attrs.get(
                "productId", iap_record.product_id
            )
            iap_record.iap_type = attrs.get(
                "inAppPurchaseType", iap_record.iap_type
            )
        else:
            iap_record = InAppPurchase(
                app_id=app.id,
                asc_iap_id=asc_iap_id,
                name=attrs.get("name", "Unknown"),
                product_id=attrs.get("productId", ""),
                iap_type=attrs.get("inAppPurchaseType", ""),
            )
            session.add(iap_record)

    await session.flush()

    # Reload from DB for response
    result = await session.execute(
        select(InAppPurchase).where(InAppPurchase.app_id == app.id)
    )
    iaps = result.scalars().all()
    return [IAPResponse.model_validate(iap) for iap in iaps]


@router.get(
    "/{app_id}/iaps/{iap_id}/prices",
    response_model=IAPPricesResponse,
)
async def get_iap_prices(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IAPPricesResponse:
    """Fetch current prices for an IAP from ASC, sync to DB."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    territory_map = await _get_territory_map(session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        prices_data = await pricing_service.get_iap_price_schedule(
            iap.asc_iap_id
        )

    price_responses: list[IAPPricePointResponse] = []
    now = datetime.now(timezone.utc)

    for price_item in prices_data:
        territory_code = price_item.get("territory_code", "")
        territory = territory_map.get(territory_code)
        if territory is None:
            continue

        customer_price = price_item.get("customer_price", 0.0)
        proceeds = price_item.get("proceeds", 0.0)
        pp_id = price_item.get("price_point_id")
        currency_code = price_item.get("currency_code", territory.currency_code)

        # Upsert IAPPrice
        existing_price_result = await session.execute(
            select(IAPPrice).where(
                IAPPrice.iap_id == iap.id,
                IAPPrice.territory_id == territory.id,
            )
        )
        price_record = existing_price_result.scalar_one_or_none()

        if price_record:
            price_record.customer_price = customer_price
            price_record.proceeds = proceeds
            price_record.price_point_id = pp_id
            price_record.synced_at = now
        else:
            price_record = IAPPrice(
                iap_id=iap.id,
                territory_id=territory.id,
                customer_price=customer_price,
                proceeds=proceeds,
                price_point_id=pp_id,
                synced_at=now,
            )
            session.add(price_record)

        price_responses.append(
            IAPPricePointResponse(
                territory_code=territory.code,
                territory_name=territory.name,
                currency_code=currency_code,
                customer_price=customer_price,
                proceeds=proceeds,
                price_point_id=pp_id,
            )
        )

    await session.flush()

    return IAPPricesResponse(
        iap_id=iap.id,
        iap_name=iap.name,
        product_id=iap.product_id,
        prices=price_responses,
    )


# ------------------------------------------------------------------
# IAP Sync (explicit)
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/iaps/{iap_id}/sync",
    response_model=SyncPricesResponse,
)
async def sync_iap_prices(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SyncPricesResponse:
    """Explicitly sync current IAP prices from ASC into DB cache.

    Fetches the price schedule from ASC and upserts all manual prices
    into the IAPPrice table.  Same data as the GET endpoint, but
    guaranteed fresh.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)
    territory_map = await _get_territory_map(session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        prices_data = await pricing_service.get_iap_price_schedule(
            iap.asc_iap_id
        )

    now = datetime.now(timezone.utc)
    prices_synced = 0

    existing_result = await session.execute(
        select(IAPPrice).where(IAPPrice.iap_id == iap.id)
    )
    existing_by_territory: dict[int, IAPPrice] = {
        p.territory_id: p for p in existing_result.scalars().all()
    }

    for price_item in prices_data:
        territory_code = price_item.get("territory_code", "")
        territory = territory_map.get(territory_code)
        if territory is None:
            continue

        customer_price = price_item.get("customer_price", 0.0)
        proceeds = price_item.get("proceeds", 0.0)
        pp_id = price_item.get("price_point_id")

        price_record = existing_by_territory.get(territory.id)

        if price_record:
            price_record.customer_price = customer_price
            price_record.proceeds = proceeds
            price_record.price_point_id = pp_id
            price_record.synced_at = now
        else:
            price_record = IAPPrice(
                iap_id=iap.id,
                territory_id=territory.id,
                customer_price=customer_price,
                proceeds=proceeds,
                price_point_id=pp_id,
                synced_at=now,
            )
            session.add(price_record)
        prices_synced += 1

    await session.flush()

    logger.info("IAP sync complete: %d prices for iap %s", prices_synced, iap_id)
    return SyncPricesResponse(
        prices_synced=prices_synced,
        price_points_synced=0,
    )


# ------------------------------------------------------------------
# IAP Price Points (filesystem cache)
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/iaps/{iap_id}/price-points/sync",
    response_model=PricePointSyncResponse,
)
async def sync_iap_price_points(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PricePointSyncResponse:
    """Sync Apple IAP price tiers to the app-wide filesystem cache.

    Same as the subscription variant — always syncs every seeded
    territory regardless of which ones the IAP currently has prices in.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)
    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    territory_codes = sorted({t.code for t in territory_by_id.values()})

    # App-wide IAP tier cache: shared across every IAP on this app.
    # Don't clear() — Apple rate-limits this endpoint heavily; let
    # fetch_and_cache_all skip already-cached entries so a retry only
    # picks up the ones that previously failed.
    cache = PricePointCache(app.asc_app_id, product_type="iap")

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        # Apple rate-limits the IAP price-points endpoint more
        # aggressively than the subscription one — keep this serial.
        total_points = await cache.fetch_and_cache_all(
            territory_codes, iap.asc_iap_id, pricing_service,
            concurrency=1,
        )

    logger.info(
        "IAP tier sync: %d territories, %d tiers for app %s (via iap %s)",
        len(territory_codes), total_points, app.asc_app_id, iap_id,
    )
    return PricePointSyncResponse(
        territories_synced=len(territory_codes),
        price_points_total=total_points,
    )


@router.get(
    "/{app_id}/iaps/{iap_id}/price-points/status",
    response_model=PricePointCacheStatus,
)
async def get_iap_price_point_cache_status(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PricePointCacheStatus:
    """Return the status of the IAP price point filesystem cache."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    await _get_verified_iap(iap_id, app.id, session)

    cache = PricePointCache(app.asc_app_id, product_type="iap")
    info = await cache.status()
    return PricePointCacheStatus(**info)


# ------------------------------------------------------------------
# IAP Price Preview
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/iaps/{iap_id}/prices/preview",
    response_model=IAPPricePreviewResponse,
)
async def preview_iap_prices(
    app_id: int,
    iap_id: int,
    body: PricePreviewRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IAPPricePreviewResponse:
    """Preview suggested prices for an IAP based on economic index data.

    Same calculation logic as subscription preview: computes suggested
    prices for each territory and matches to the nearest Apple price
    point from the filesystem cache.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)
    territory_map = await _get_territory_map(session)

    # Load current prices from DB
    current_prices_result = await session.execute(
        select(IAPPrice).where(IAPPrice.iap_id == iap.id)
    )
    current_prices = current_prices_result.scalars().all()
    # Re-use the same type annotation; IAPPrice has the same shape
    current_price_by_territory: dict[int, Any] = {
        p.territory_id: p for p in current_prices
    }

    # Load app-wide IAP tier ladder, enriched with this IAP's IDs.
    cache = PricePointCache(app.asc_app_id, product_type="iap")
    price_points_by_territory: dict[str, list[dict]] = {}
    all_territories = _unique_territories(territory_map)
    for territory in all_territories:
        cached = await cache.get_with_price_point_ids(
            territory.code, iap.asc_iap_id,
        )
        if cached is not None:
            price_points_by_territory[territory.code] = cached

    preview_items: list[PricePreviewItem] = []

    if body.index_type == "exchange_rate":
        from decimal import Decimal

        from app.core.config import settings
        from app.services.pricing.currency_rounding import apply_currency_rounding
        from app.services.pricing.vat import apply_vat
        from app.services.rates import RateCacheClient, RateCacheError

        base_territory = territory_map.get(body.base_territory_code)
        if base_territory is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base territory '{body.base_territory_code}' not found",
            )
        base_currency = effective_currency(
            base_territory, price_points_by_territory.get(base_territory.code),
        )

        try:
            rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
            rates = await rate_client.get_rates(base=base_currency)
        except RateCacheError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch exchange rates: {exc}",
            )

        for territory in all_territories:
            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            if currency == base_currency:
                rate = 1.0
            else:
                rate = rates.get(currency)
                if rate is None:
                    continue

            suggested_decimal = Decimal(str(body.base_price)) * Decimal(str(rate))

            if body.apply_vat and territory.vat_rate and territory.vat_rate > 0:
                suggested_decimal = apply_vat(
                    suggested_decimal, territory.vat_rate
                )

            if body.charming_mode == "smart":
                suggested_decimal = apply_currency_rounding(
                    suggested_decimal, currency
                )
                suggested = float(suggested_decimal)
            else:
                suggested = _apply_charming(
                    float(suggested_decimal), body.charming_mode, currency
                )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))
    elif body.index_type == "gdp_brackets":
        # --- GDP-bracket branch (mirrors subscription preview) ---
        from decimal import Decimal

        from app.core.config import settings
        from app.services.pricing.currency_rounding import apply_currency_rounding
        from app.services.pricing.gdp_brackets import assign_tier
        from app.services.pricing.vat import apply_vat
        from app.services.rates import RateCacheClient, RateCacheError

        assert body.gdp_config is not None  # validator enforced

        gdp_indices_result = await session.execute(
            select(EconomicIndex).where(
                EconomicIndex.index_type == "gdp_per_capita_ppp"
            )
        )
        gdp_by_territory_id: dict[int, float] = {
            idx.territory_id: idx.value
            for idx in gdp_indices_result.scalars().all()
        }

        try:
            rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
            rates = await rate_client.get_rates(base="USD")
        except RateCacheError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch exchange rates: {exc}",
            )

        for territory in all_territories:
            tier = assign_tier(
                territory.code,
                gdp_by_territory_id.get(territory.id),
                body.gdp_config,
            )
            tier_price_usd = body.gdp_config.tier_prices_usd[tier]

            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            if currency == "USD":
                rate = Decimal("1")
            else:
                rate_value = rates.get(currency)
                if rate_value is None:
                    continue
                rate = Decimal(str(rate_value))

            suggested_decimal = tier_price_usd * rate

            if body.apply_vat and territory.vat_rate and territory.vat_rate > 0:
                suggested_decimal = apply_vat(
                    suggested_decimal, territory.vat_rate,
                )

            if body.charming_mode == "smart":
                suggested_decimal = apply_currency_rounding(
                    suggested_decimal, currency,
                )
                suggested = float(suggested_decimal)
            else:
                suggested = _apply_charming(
                    float(suggested_decimal), body.charming_mode, currency,
                )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))
    else:
        indices_result = await session.execute(
            select(EconomicIndex).where(
                EconomicIndex.index_type == body.index_type
            )
        )
        indices = indices_result.scalars().all()

        index_by_territory: dict[int, float] = {
            idx.territory_id: idx.value for idx in indices
        }

        base_territory = territory_map.get(body.base_territory_code)
        if base_territory is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base territory '{body.base_territory_code}' not found",
            )
        base_index_value = index_by_territory.get(base_territory.id)
        if base_index_value is None or base_index_value == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"No {body.index_type} index data for territory "
                    f"'{body.base_territory_code}'"
                ),
            )

        for territory in all_territories:
            territory_index = index_by_territory.get(territory.id)
            if territory_index is None:
                continue

            currency = effective_currency(
                territory, price_points_by_territory.get(territory.code),
            )
            suggested = body.base_price * (territory_index / base_index_value)

            suggested = _apply_charming(
                suggested, body.charming_mode, currency,
            )

            preview_items.append(_build_preview_item(
                territory=territory,
                currency_code=currency,
                suggested=suggested,
                current_price_by_territory=current_price_by_territory,
                price_points_by_territory=price_points_by_territory,
            ))

    return IAPPricePreviewResponse(
        iap_id=iap.id,
        iap_name=iap.name,
        index_type=body.index_type,
        base_price=body.base_price,
        items=preview_items,
    )


# ------------------------------------------------------------------
# IAP Resolve Manual Price
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/iaps/{iap_id}/prices/resolve",
    response_model=PriceResolveResponse,
)
async def resolve_iap_manual_price(
    app_id: int,
    iap_id: int,
    body: PriceResolveRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PriceResolveResponse:
    """Resolve a manual price to the nearest Apple IAP price tier.

    Reads from the filesystem price point cache -- no ASC API calls.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    cache = PricePointCache(app.asc_app_id, product_type="iap")
    pps = await cache.get_with_price_point_ids(
        body.territory_code, iap.asc_iap_id,
    )
    if not pps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No cached price tiers for territory '{body.territory_code}'. "
                f"Sync price tiers first."
            ),
        )

    nearest = min(pps, key=lambda pp: abs(pp["customer_price"] - body.price))
    return PriceResolveResponse(
        territory_code=body.territory_code,
        currency_code=nearest["currency_code"],
        customer_price=nearest["customer_price"],
        proceeds=nearest["proceeds"],
        price_point_id=nearest["price_point_id"],
    )


# ------------------------------------------------------------------
# IAP Apply Prices
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/iaps/{iap_id}/prices/apply",
    response_model=PriceApplyResponse,
)
async def apply_iap_prices(
    app_id: int,
    iap_id: int,
    body: PriceApplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PriceApplyResponse:
    """Apply selected price points to an IAP via ASC API.

    IAP pricing uses ``inAppPurchasePriceSchedules`` which requires
    all territory prices to be submitted at once.  Includes the same
    per-territory ±50% safety check as subscriptions; territories
    that fail the check are excluded from the batch unless their
    item has ``force=True``.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    # Load current prices from DB for the safety check
    current_prices_result = await session.execute(
        select(IAPPrice).where(IAPPrice.iap_id == iap.id)
    )
    current_prices = current_prices_result.scalars().all()
    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    current_price_by_code: dict[str, float] = {}
    for p in current_prices:
        territory = territory_by_id.get(p.territory_id)
        if territory:
            current_price_by_code[territory.code] = p.customer_price

    cache = PricePointCache(app.asc_app_id, product_type="iap")

    applied = 0
    failed = 0
    skipped = 0
    errors: list[str] = []
    skipped_items: list[PriceApplySkippedItem] = []
    price_entries: list[dict] = []

    for item in body.items:
        tc = item.territory_code
        current_price = current_price_by_code.get(tc)

        # Ensure tier ladder is cached (on-demand fetch if missing).
        territory_pps = await cache.get_with_price_point_ids(
            tc, iap.asc_iap_id,
        )
        if territory_pps is None:
            async with await _get_asc_client_for_app(app, session) as client:
                pricing_service = ASCPricingService(client)
                try:
                    await cache.fetch_and_cache(
                        tc, iap.asc_iap_id, pricing_service,
                    )
                    territory_pps = await cache.get_with_price_point_ids(
                        tc, iap.asc_iap_id,
                    ) or []
                except Exception:
                    logger.warning(
                        "Failed to fetch IAP price tiers for %s", tc,
                        exc_info=True,
                    )
                    failed += 1
                    errors.append(
                        f"Territory {tc}: failed to fetch price tiers"
                    )
                    continue

        # Look up the price for the chosen price_point_id
        new_price: float | None = None
        for pp in territory_pps:
            if pp["price_point_id"] == item.price_point_id:
                new_price = pp["customer_price"]
                break

        # Reject unknown price_point_ids
        if new_price is None:
            failed += 1
            errors.append(
                f"Territory {tc}: price_point_id "
                f"{item.price_point_id!r} not found in cached tiers"
            )
            continue

        # Safety check: skip if change exceeds ±50% (unless item.force)
        if (
            not item.force
            and current_price is not None
            and current_price > 0
            and (
                new_price > current_price * SAFETY_MAX_UP
                or new_price < current_price * SAFETY_MAX_DOWN
            )
        ):
            diff_pct = round(
                ((new_price - current_price) / current_price) * 100, 2
            )
            skipped += 1
            skipped_items.append(
                PriceApplySkippedItem(
                    territory_code=tc,
                    reason=(
                        f"Price change {diff_pct:+}% exceeds "
                        f"safety limit ({SAFETY_LABEL})"
                    ),
                    current_price=current_price,
                    new_price=new_price,
                    diff_percent=diff_pct,
                )
            )
            logger.info(
                "Skipped IAP %s territory %s: %+.1f%% (%.2f -> %.2f)",
                iap_id, tc, diff_pct, current_price, new_price,
            )
            continue

        price_entries.append({
            "territory_code": tc,
            "price_point_id": item.price_point_id,
        })

    # Apple replaces the entire iapPriceSchedule on POST: any territory
    # not in the new manualPrices list reverts to auto-equalization.
    # Preserve previously-manual territories that the user didn't touch
    # (and ensure the base territory is always present — Apple rejects
    # the schedule otherwise).
    submitted_codes = {entry["territory_code"] for entry in price_entries}
    for p in current_prices:
        territory = territory_by_id.get(p.territory_id)
        if territory is None or territory.code in submitted_codes:
            continue
        if not p.price_point_id:
            continue
        price_entries.append({
            "territory_code": territory.code,
            "price_point_id": p.price_point_id,
        })

    # Submit all accepted prices in a single batch via price schedule
    if price_entries:
        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.set_iap_price(
                    iap_id=iap.asc_iap_id,
                    price_entries=price_entries,
                )
                applied = len(price_entries)
            except ASCAPIError as exc:
                failed += len(price_entries)
                errors.append(f"Batch apply failed: {exc.message}")
                logger.warning(
                    "Failed to apply IAP prices for iap %s: %s",
                    iap_id, exc.message,
                )

    return PriceApplyResponse(
        applied=applied,
        failed=failed,
        skipped=skipped,
        errors=errors,
        skipped_items=skipped_items,
    )


# ------------------------------------------------------------------
# Localization helpers
# ------------------------------------------------------------------


def _parse_localization(resource: dict) -> LocalizationResponse:
    """Convert a JSON:API localization resource to a response schema."""
    attrs = resource.get("attributes", {})
    return LocalizationResponse(
        id=resource["id"],
        locale=attrs.get("locale", ""),
        name=attrs.get("name", ""),
        description=attrs.get("description", ""),
    )


async def _bulk_sync_localizations(
    existing: list[dict],
    requested: list[LocalizationCreate],
    create_fn,
    update_fn,
) -> BulkLocalizationResponse:
    """Sync localizations: update existing locales, create missing ones.

    Args:
        existing: Current JSON:API localization resources from ASC.
        requested: Desired localization entries.
        create_fn: Coroutine ``(locale, name, description) -> dict``.
        update_fn: Coroutine ``(localization_id, name, description) -> dict``.

    Returns:
        BulkLocalizationResponse with counts and final state.
    """
    existing_by_locale: dict[str, dict] = {}
    for item in existing:
        locale = item.get("attributes", {}).get("locale", "")
        if locale:
            existing_by_locale[locale] = item

    created = 0
    updated = 0
    results: list[LocalizationResponse] = []

    for loc in requested:
        if loc.locale in existing_by_locale:
            resource = await update_fn(
                existing_by_locale[loc.locale]["id"],
                loc.name,
                loc.description,
            )
            updated += 1
        else:
            resource = await create_fn(loc.locale, loc.name, loc.description)
            created += 1
        results.append(_parse_localization(resource.get("data", resource)))

    # Include untouched existing localizations in the response
    touched_locales = {loc.locale for loc in requested}
    for locale, item in existing_by_locale.items():
        if locale not in touched_locales:
            results.append(_parse_localization(item))

    return BulkLocalizationResponse(
        created=created,
        updated=updated,
        localizations=results,
    )


# ------------------------------------------------------------------
# Subscription Localization endpoints
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/localizations",
    response_model=list[LocalizationResponse],
)
async def list_subscription_localizations(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LocalizationResponse]:
    """Fetch all localizations for a subscription from ASC."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        data = await pricing_service.list_subscription_localizations(
            subscription.asc_subscription_id
        )

    return [_parse_localization(item) for item in data]


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/localizations",
    response_model=LocalizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription_localization(
    app_id: int,
    subscription_id: int,
    body: LocalizationCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LocalizationResponse:
    """Create a localization for a subscription."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_subscription_localization(
                subscription.asc_subscription_id,
                body.locale,
                body.name,
                body.description,
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    return _parse_localization(result.get("data", result))


@router.put(
    "/{app_id}/subscriptions/{subscription_id}/localizations/{localization_id}",
    response_model=LocalizationResponse,
)
async def update_subscription_localization(
    app_id: int,
    subscription_id: int,
    localization_id: str,
    body: LocalizationUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LocalizationResponse:
    """Update a subscription localization (locale is immutable)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Verify subscription belongs to this app (authorization check)
    await _get_verified_subscription(subscription_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.update_subscription_localization(
                localization_id,
                body.name,
                body.description,
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    return _parse_localization(result.get("data", result))


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/localizations/bulk",
    response_model=BulkLocalizationResponse,
)
async def bulk_sync_subscription_localizations(
    app_id: int,
    subscription_id: int,
    body: BulkLocalizationRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BulkLocalizationResponse:
    """Bulk create/update subscription localizations.

    For each item: updates if locale already exists, creates if not.
    Returns all localizations after sync.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        asc_sub_id = subscription.asc_subscription_id

        existing = await pricing_service.list_subscription_localizations(
            asc_sub_id
        )

        return await _bulk_sync_localizations(
            existing=existing,
            requested=body.localizations,
            create_fn=lambda locale, name, desc: (
                pricing_service.create_subscription_localization(
                    asc_sub_id, locale, name, desc
                )
            ),
            update_fn=lambda loc_id, name, desc: (
                pricing_service.update_subscription_localization(
                    loc_id, name, desc
                )
            ),
        )


# ------------------------------------------------------------------
# IAP Localization endpoints
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/iaps/{iap_id}/localizations",
    response_model=list[LocalizationResponse],
)
async def list_iap_localizations(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LocalizationResponse]:
    """Fetch all localizations for an IAP from ASC."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        data = await pricing_service.list_iap_localizations(iap.asc_iap_id)

    return [_parse_localization(item) for item in data]


@router.post(
    "/{app_id}/iaps/{iap_id}/localizations",
    response_model=LocalizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_iap_localization(
    app_id: int,
    iap_id: int,
    body: LocalizationCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LocalizationResponse:
    """Create a localization for an IAP."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_iap_localization(
                iap.asc_iap_id,
                body.locale,
                body.name,
                body.description,
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    return _parse_localization(result.get("data", result))


@router.put(
    "/{app_id}/iaps/{iap_id}/localizations/{localization_id}",
    response_model=LocalizationResponse,
)
async def update_iap_localization(
    app_id: int,
    iap_id: int,
    localization_id: str,
    body: LocalizationUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LocalizationResponse:
    """Update an IAP localization (locale is immutable)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Verify IAP belongs to this app (authorization check)
    await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.update_iap_localization(
                localization_id,
                body.name,
                body.description,
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    return _parse_localization(result.get("data", result))


@router.post(
    "/{app_id}/iaps/{iap_id}/localizations/bulk",
    response_model=BulkLocalizationResponse,
)
async def bulk_sync_iap_localizations(
    app_id: int,
    iap_id: int,
    body: BulkLocalizationRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BulkLocalizationResponse:
    """Bulk create/update IAP localizations.

    For each item: updates if locale already exists, creates if not.
    Returns all localizations after sync.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        asc_iap_id = iap.asc_iap_id

        existing = await pricing_service.list_iap_localizations(asc_iap_id)

        return await _bulk_sync_localizations(
            existing=existing,
            requested=body.localizations,
            create_fn=lambda locale, name, desc: (
                pricing_service.create_iap_localization(
                    asc_iap_id, locale, name, desc
                )
            ),
            update_fn=lambda loc_id, name, desc: (
                pricing_service.update_iap_localization(
                    loc_id, name, desc
                )
            ),
        )


# ------------------------------------------------------------------
# Review Screenshot endpoints
# ------------------------------------------------------------------


def _parse_screenshot(data: dict | None) -> ReviewScreenshotResponse | None:
    """Convert ASC screenshot resource to response schema."""
    if data is None:
        return None
    attrs = data.get("attributes", {})
    image_asset = attrs.get("imageAsset", {})
    image_url = None
    if image_asset and image_asset.get("templateUrl"):
        image_url = (
            image_asset["templateUrl"]
            .replace("{w}", str(image_asset.get("width", 640)))
            .replace("{h}", str(image_asset.get("height", 920)))
            .replace("{f}", "png")
        )
    return ReviewScreenshotResponse(
        id=data["id"],
        file_name=attrs.get("fileName", ""),
        file_size=attrs.get("fileSize", 0),
        image_url=image_url,
    )


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/review-screenshot",
    response_model=ReviewScreenshotResponse | None,
)
async def get_subscription_review_screenshot(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewScreenshotResponse | None:
    """Get the review screenshot for a subscription."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        data = await pricing_service.get_subscription_review_screenshot(
            subscription.asc_subscription_id
        )
    return _parse_screenshot(data)


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/review-screenshot",
    response_model=ReviewScreenshotResponse,
)
async def upload_subscription_review_screenshot(
    app_id: int,
    subscription_id: int,
    file: UploadFile,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewScreenshotResponse:
    """Upload a review screenshot for a subscription."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(subscription_id, app.id, session)

    file_bytes = await file.read()
    file_name = file.filename or "screenshot.png"

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.upload_subscription_review_screenshot(
                subscription.asc_subscription_id, file_name, file_bytes
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Screenshot upload failed: {exc.message}",
            )
    return _parse_screenshot(result.get("data"))  # type: ignore[return-value]


@router.get(
    "/{app_id}/iaps/{iap_id}/review-screenshot",
    response_model=ReviewScreenshotResponse | None,
)
async def get_iap_review_screenshot(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewScreenshotResponse | None:
    """Get the review screenshot for an IAP."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        data = await pricing_service.get_iap_review_screenshot(iap.asc_iap_id)
    return _parse_screenshot(data)


@router.post(
    "/{app_id}/iaps/{iap_id}/review-screenshot",
    response_model=ReviewScreenshotResponse,
)
async def upload_iap_review_screenshot(
    app_id: int,
    iap_id: int,
    file: UploadFile,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewScreenshotResponse:
    """Upload a review screenshot for an IAP."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    file_bytes = await file.read()
    file_name = file.filename or "screenshot.png"

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.upload_iap_review_screenshot(
                iap.asc_iap_id, file_name, file_bytes
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Screenshot upload failed: {exc.message}",
            )
    return _parse_screenshot(result.get("data"))  # type: ignore[return-value]
