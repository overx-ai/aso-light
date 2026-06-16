"""Pricing API endpoints for subscriptions and in-app purchases."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, get_args

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
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
    GroupLocalizationCreate,
    GroupLocalizationResponse,
    GroupLocalizationUpdate,
    IAPPricePointResponse,
    IAPPricePreviewResponse,
    IAPPricesResponse,
    IAPResponse,
    IntroOfferCreate,
    IntroOfferDuration,
    IntroOfferMode,
    IntroOfferResponse,
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
    SubscriptionAvailabilityResponse,
    SubscriptionCreate,
    SubscriptionGroupCreate,
    SubscriptionGroupResponse,
    SubscriptionGroupUpdate,
    SubscriptionGroupWithSubscriptionsResponse,
    SubscriptionPricesResponse,
    SubscriptionResponse,
    SubscriptionUpdate,
    SyncPricesResponse,
)
from app.data.territories import ALPHA2_TO_ALPHA3
from app.services.asc.availability import ASCAvailabilityService
from app.services.asc.client import ASCClient
from app.services.asc.errors import ASCAPIError, ChildResourceNotFoundError
from app.services.asc.price_point_cache import PricePointCache
from app.services.asc.pricing import ASCPricingService
from app.services.pricing.currency import effective_currency
from app.services.pricing.preview import build_preview_items
from app.services.pricing.safety import (
    exceeds_safety_band,
    safety_skip_item,
)

# Reverse map: Apple territory IDs are alpha-3 (USA, GBR, ARE), our DB
# stores alpha-2 (US, GB, AE). Use this when parsing JSON:API responses
# whose `territories` resource id is alpha-3.
ALPHA3_TO_ALPHA2: dict[str, str] = {v: k for k, v in ALPHA2_TO_ALPHA3.items()}

# Valid enum members (derived from the Literal types so they never drift)
# used to defensively coerce unrecognized ASC intro-offer values to None.
_INTRO_OFFER_MODES: frozenset[str] = frozenset(get_args(IntroOfferMode))
_INTRO_OFFER_DURATIONS: frozenset[str] = frozenset(get_args(IntroOfferDuration))

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_iap_base_territory(
    requested_alpha2: str, price_entries: list[dict],
) -> str | None:
    """Pick the alpha-3 baseTerritory for an IAP price schedule.

    Apple rejects an ``inAppPurchasePriceSchedule`` whose ``baseTerritory``
    has no manual price in the submission. Prefer the requested alpha-2
    territory when it is both valid and present in ``price_entries``;
    otherwise fall back to the first submitted territory that maps to an
    alpha-3. Returns ``None`` only when no submitted territory maps (the
    caller then skips the submit). Mirrors the IAP clone path.
    """
    final_codes = {entry["territory_code"] for entry in price_entries}
    base_alpha3 = ALPHA2_TO_ALPHA3.get(requested_alpha2)
    if base_alpha3 is not None and requested_alpha2 in final_codes:
        return base_alpha3
    for entry in price_entries:
        code = entry["territory_code"]
        if code in ALPHA2_TO_ALPHA3:
            return ALPHA2_TO_ALPHA3[code]
    return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


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


async def _get_verified_subscription_group(
    group_id: int, app_id: int, session: AsyncSession
) -> SubscriptionGroup:
    """Load a SubscriptionGroup and verify it belongs to the given app."""
    result = await session.execute(
        select(SubscriptionGroup).where(
            SubscriptionGroup.id == group_id,
            SubscriptionGroup.app_id == app_id,
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription group not found for this app",
        )
    return group


async def _resolve_app_target_territories(
    client: ASCClient,
    app_asc_id: str,
) -> tuple[list[str], bool]:
    """Determine the alpha-3 territories a sub should be available in.

    Source priority:
    1. The app's own ``appAvailabilityV2`` (the canonical "where the app
       sells") if Apple has the resource.
    2. Apple's full ``/v1/territories`` catalog (175) if (1) returns 404
       — some apps never had appAvailabilityV2 initialized server-side
       and would otherwise produce subs with zero territories.

    Returns ``(alpha3_codes, available_in_new_territories)``. Note that
    apps without appAvailabilityV2 default to ``True`` for the new-
    territories flag (Apple's safest default).
    """
    availability_service = ASCAvailabilityService(client)
    try:
        app_av = await availability_service.get_app_availability(app_asc_id)
        alpha3 = sorted(
            ALPHA2_TO_ALPHA3[t["territory_code"]]
            for t in app_av["territories"]
            if t["available"] and t["territory_code"] in ALPHA2_TO_ALPHA3
        )
        return alpha3, app_av["available_in_new_territories"]
    except ASCAPIError as exc:
        if exc.status_code != 404:
            raise
        # Fall back to Apple's full catalog.
        terrs = await client._get_all_pages(
            "/territories", params={"limit": 200}
        )
        return sorted({t["id"] for t in terrs}), True


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

    pp_cache = PricePointCache()
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


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/availability",
    response_model=SubscriptionAvailabilityResponse,
)
async def get_subscription_availability(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionAvailabilityResponse:
    """Return alpha-2 codes of territories the subscription is available in.

    Sourced live from ASC (no DB caching). Used by the pricing UI to
    flag territories that are available but missing a price.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            alpha3 = await pricing_service.list_subscription_availability(
                subscription.asc_subscription_id
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    territories = sorted(
        ALPHA3_TO_ALPHA2[code] for code in alpha3 if code in ALPHA3_TO_ALPHA2
    )
    return SubscriptionAvailabilityResponse(
        subscription_id=subscription.id,
        territories=territories,
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
    cache = PricePointCache()

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

    cache = PricePointCache()
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
    cache = PricePointCache()
    price_points_by_territory: dict[str, list[dict]] = {}
    all_territories = _unique_territories(territory_map)
    for territory in all_territories:
        cached = await cache.get_with_price_point_ids(
            territory.code, subscription.asc_subscription_id,
        )
        if cached is not None:
            price_points_by_territory[territory.code] = cached

    preview_items, skipped_territories = await build_preview_items(
        body=body,
        session=session,
        territory_map=territory_map,
        all_territories=all_territories,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        build_item=_build_preview_item,
        raise_error=_preview_error,
    )

    return PricePreviewResponse(
        subscription_id=subscription.id,
        subscription_name=subscription.name,
        index_type=body.index_type,
        base_price=body.base_price,
        items=preview_items,
        skipped_territories=skipped_territories,
    )


def _preview_error(message: str) -> HTTPException:
    """Map a shared-preview error message to the right HTTP status.

    Upstream FX-rate failures are a bad gateway (502); everything else is
    a client error (400, e.g. unknown base territory or missing index).
    """
    if message.startswith("Failed to fetch exchange rates"):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=message,
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=message,
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
        and exceeds_safety_band(current_price, compare_price)
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

    cache = PricePointCache()
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

    cache = PricePointCache()

    applied = 0
    failed = 0
    skipped = 0
    errors: list[str] = []
    skipped_items: list[PriceApplySkippedItem] = []
    applied_alpha2: list[str] = []

    logger.info(
        "Apply request: subscription=%s items=%d sample=%s intro_offer=%s",
        subscription_id,
        len(body.items),
        [i.territory_code for i in body.items[:5]],
        body.intro_offer.model_dump() if body.intro_offer else None,
    )

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

            # Safety check: skip if change exceeds ±50% (unless item.force).
            # ``new_price`` is guaranteed non-None here (the unknown-id
            # branch above ``continue``s).
            skip = safety_skip_item(
                tc,
                current_price=current_price,
                new_price=new_price,
                force=item.force,
            )
            if skip is not None:
                skipped += 1
                skipped_items.append(skip)
                logger.info(
                    "Skipped %s: %+.1f%% (%.2f → %.2f)",
                    tc, skip.diff_percent, skip.current_price, skip.new_price,
                )
                continue

            try:
                await pricing_service.create_subscription_price(
                    subscription_id=subscription.asc_subscription_id,
                    price_point_id=item.price_point_id,
                )
                applied += 1
                applied_alpha2.append(tc)
            except ASCAPIError as exc:
                failed += 1
                errors.append(
                    f"Territory {tc}: {exc.message}"
                )
                logger.warning(
                    "Failed to apply price for subscription %s, "
                    "territory %s (status %d): %s | apple_body=%s",
                    subscription_id, tc, exc.status_code, exc.message,
                    exc.response_body,
                )

        # Optional: bundled free-trial intro offer.
        # Apple rejects worldwide intro offers (ENTITY_ERROR.RELATIONSHIP.
        # REQUIRED — territory is mandatory) and has no PATCH endpoint —
        # only delete + create per territory. We diff existing vs target
        # so a re-apply only touches territories whose offer is missing
        # or has a different config; otherwise it's a no-op.
        intro_offer_synced = False
        intro_offer_error: str | None = None
        intro_offer_applied = 0
        intro_offer_failed = 0
        intro_offer_kept = 0
        intro_offer_deleted = 0
        if body.intro_offer is not None:
            try:
                existing_offers = (
                    await pricing_service.list_subscription_introductory_offers(
                        subscription.asc_subscription_id
                    )
                )
            except ASCAPIError as exc:
                intro_offer_error = exc.message
                logger.warning(
                    "Failed to list existing intro offers for subscription "
                    "%s (status %d): %s | apple_body=%s",
                    subscription_id, exc.status_code, exc.message,
                    exc.response_body,
                )
                existing_offers = []

            if intro_offer_error is None:
                # Target every territory the sub is now priced in. The DB
                # cache covers prices that already existed before this
                # apply; ``applied_alpha2`` covers ones we just POSTed
                # (important when this is the sub's *first* apply — the
                # DB cache is empty until the next sync).
                priced_rows = await session.execute(
                    select(SubscriptionPrice.territory_id).where(
                        SubscriptionPrice.subscription_id == subscription.id
                    )
                )
                cached_alpha2 = {
                    territory_by_id[tid].code
                    for (tid,) in priced_rows.all()
                    if tid in territory_by_id
                }
                target_alpha2 = sorted(cached_alpha2.union(applied_alpha2))
                target_set = set(target_alpha2)

                # Index existing offers by alpha-2 territory.
                existing_by_alpha2: dict[str, dict] = {}
                for item_dict in existing_offers:
                    resource = item_dict.get("resource") or {}
                    offer_id = resource.get("id")
                    attrs = resource.get("attributes") or {}
                    rel = (resource.get("relationships") or {}).get("territory") or {}
                    terr_alpha3 = (rel.get("data") or {}).get("id")
                    terr_alpha2 = (
                        ALPHA3_TO_ALPHA2.get(terr_alpha3) if terr_alpha3 else None
                    )
                    if not offer_id or not terr_alpha2:
                        continue
                    existing_by_alpha2[terr_alpha2] = {
                        "offer_id": offer_id,
                        "offer_mode": attrs.get("offerMode"),
                        "duration": attrs.get("duration"),
                        "number_of_periods": attrs.get("numberOfPeriods"),
                    }

                desired_mode = "FREE_TRIAL"
                desired_duration = body.intro_offer.duration
                desired_periods = body.intro_offer.number_of_periods

                # Diff.
                to_delete_ids: list[str] = []
                to_create_alpha2: list[str] = []

                for alpha2 in target_alpha2:
                    existing = existing_by_alpha2.get(alpha2)
                    if existing is None:
                        to_create_alpha2.append(alpha2)
                    elif (
                        existing["offer_mode"] != desired_mode
                        or existing["duration"] != desired_duration
                        or existing["number_of_periods"] != desired_periods
                    ):
                        to_delete_ids.append(existing["offer_id"])
                        to_create_alpha2.append(alpha2)
                    else:
                        intro_offer_kept += 1

                # Orphans: existing offers in territories no longer priced.
                for alpha2, existing in existing_by_alpha2.items():
                    if alpha2 not in target_set:
                        to_delete_ids.append(existing["offer_id"])

                sem = asyncio.Semaphore(2)

                async def _delete_one(offer_id: str) -> bool:
                    async with sem:
                        try:
                            await pricing_service.delete_subscription_introductory_offer(
                                offer_id
                            )
                            return True
                        except ASCAPIError as exc:
                            logger.warning(
                                "Failed to delete intro offer %s for "
                                "subscription %s (status %d): %s | apple_body=%s",
                                offer_id, subscription_id, exc.status_code,
                                exc.message, exc.response_body,
                            )
                            return False

                async def _create_one(alpha2: str) -> bool:
                    alpha3 = ALPHA2_TO_ALPHA3.get(alpha2)
                    if alpha3 is None:
                        return False
                    async with sem:
                        try:
                            await pricing_service.create_subscription_introductory_offer(
                                subscription_id=subscription.asc_subscription_id,
                                offer_mode=desired_mode,
                                duration=desired_duration,
                                number_of_periods=desired_periods,
                                territory_id=alpha3,
                            )
                            return True
                        except ASCAPIError as exc:
                            logger.warning(
                                "Failed to apply intro offer for subscription "
                                "%s, territory %s (status %d): %s | "
                                "apple_body=%s",
                                subscription_id, alpha2, exc.status_code,
                                exc.message, exc.response_body,
                            )
                            return False

                if to_delete_ids:
                    delete_results = await asyncio.gather(
                        *[_delete_one(oid) for oid in to_delete_ids],
                        return_exceptions=False,
                    )
                    intro_offer_deleted = sum(1 for ok in delete_results if ok)

                if to_create_alpha2:
                    create_results = await asyncio.gather(
                        *[_create_one(c) for c in to_create_alpha2],
                        return_exceptions=False,
                    )
                    intro_offer_applied = sum(1 for ok in create_results if ok)
                    intro_offer_failed = len(create_results) - intro_offer_applied

                intro_offer_synced = (
                    intro_offer_applied > 0
                    or (intro_offer_kept > 0 and not to_create_alpha2)
                )
                if intro_offer_failed > 0 and intro_offer_error is None:
                    intro_offer_error = (
                        f"{intro_offer_failed} of "
                        f"{intro_offer_applied + intro_offer_failed} "
                        f"territories failed"
                    )

                logger.info(
                    "Intro offer diff for subscription %s: "
                    "target=%d kept=%d deleted=%d created=%d failed=%d "
                    "(priced_in_db=%d applied_this_run=%d)",
                    subscription_id, len(target_alpha2), intro_offer_kept,
                    intro_offer_deleted, intro_offer_applied,
                    intro_offer_failed, len(cached_alpha2), len(applied_alpha2),
                )

    return PriceApplyResponse(
        applied=applied,
        failed=failed,
        skipped=skipped,
        errors=errors,
        skipped_items=skipped_items,
        intro_offer_synced=intro_offer_synced,
        intro_offer_failed=intro_offer_failed,
        intro_offer_error=intro_offer_error,
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
    cache = PricePointCache(product_type="iap")

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

    cache = PricePointCache(product_type="iap")
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
    cache = PricePointCache(product_type="iap")
    price_points_by_territory: dict[str, list[dict]] = {}
    all_territories = _unique_territories(territory_map)
    for territory in all_territories:
        cached = await cache.get_with_price_point_ids(
            territory.code, iap.asc_iap_id,
        )
        if cached is not None:
            price_points_by_territory[territory.code] = cached

    preview_items, skipped_territories = await build_preview_items(
        body=body,
        session=session,
        territory_map=territory_map,
        all_territories=all_territories,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        build_item=_build_preview_item,
        raise_error=_preview_error,
    )

    return IAPPricePreviewResponse(
        iap_id=iap.id,
        iap_name=iap.name,
        index_type=body.index_type,
        base_price=body.base_price,
        items=preview_items,
        skipped_territories=skipped_territories,
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

    cache = PricePointCache(product_type="iap")
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

    # Apple replaces the ENTIRE iapPriceSchedule on every apply. The
    # preserve loop below can only re-add territories we already have a
    # cached price_point_id for; with no cache, a partial apply would
    # silently reset every untouched territory to auto-equalization.
    if not current_prices:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Sync IAP prices before applying — the schedule replace "
                "would reset untouched territories."
            ),
        )

    territory_map = await _get_territory_map(session)
    territory_by_id = {t.id: t for t in territory_map.values()}

    current_price_by_code: dict[str, float] = {}
    for p in current_prices:
        territory = territory_by_id.get(p.territory_id)
        if territory:
            current_price_by_code[territory.code] = p.customer_price

    cache = PricePointCache(product_type="iap")

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
        skip = safety_skip_item(
            tc,
            current_price=current_price,
            new_price=new_price,
            force=item.force,
        )
        if skip is not None:
            skipped += 1
            skipped_items.append(skip)
            logger.info(
                "Skipped IAP %s territory %s: %+.1f%% (%.2f -> %.2f)",
                iap_id, tc, skip.diff_percent, skip.current_price,
                skip.new_price,
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
    # the schedule otherwise). ``submitted_count`` is the user-facing
    # ``applied`` total; preserved territories are padding, not changes.
    submitted_codes = {entry["territory_code"] for entry in price_entries}
    submitted_count = len(price_entries)
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

    # Apple's price schedule requires a ``baseTerritory`` with a manual
    # price in the submission; derive it from the requested base (alpha-2),
    # falling back to the first priced territory.
    base_alpha3 = _resolve_iap_base_territory(
        body.base_territory_code, price_entries,
    )

    # Submit all accepted prices in a single batch via price schedule
    if price_entries and base_alpha3 is not None:
        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.set_iap_price(
                    iap_id=iap.asc_iap_id,
                    price_entries=price_entries,
                    base_territory_alpha3=base_alpha3,
                )
                applied = submitted_count
            except ASCAPIError as exc:
                failed += submitted_count
                errors.append(f"Batch apply failed: {exc.message}")
                logger.warning(
                    "Failed to apply IAP prices for iap %s (status %d): %s | "
                    "apple_body=%s",
                    iap_id, exc.status_code, exc.message, exc.response_body,
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
    """Convert a JSON:API localization resource to a response schema.

    Includes ``state`` so clients can filter REJECTED localizations during
    recovery flows. Mirrors ``_parse_group_localization`` which has always
    surfaced state.
    """
    attrs = resource.get("attributes", {})
    return LocalizationResponse(
        id=resource["id"],
        locale=attrs.get("locale", ""),
        name=attrs.get("name", ""),
        description=attrs.get("description", ""),
        state=attrs.get("state"),
    )


def _parse_group_localization(resource: dict) -> GroupLocalizationResponse:
    """Convert a JSON:API subscriptionGroupLocalization to a response schema."""
    attrs = resource.get("attributes", {})
    return GroupLocalizationResponse(
        id=resource["id"],
        locale=attrs.get("locale", ""),
        name=attrs.get("name", ""),
        custom_app_name=attrs.get("customAppName"),
        state=attrs.get("state"),
    )


def _parse_intro_offer(item: dict) -> IntroOfferResponse:
    """Convert an intro offer + included payload to a response schema.

    ``item`` is the dict shape produced by
    ``ASCPricingService.list_subscription_introductory_offers``:
    ``{"resource": <offer>, "included": [<territory|pricePoint>]}``.
    """
    resource = item["resource"]
    attrs = resource.get("attributes", {})
    rels = resource.get("relationships", {}) or {}

    territory_id = (
        rels.get("territory", {}).get("data", {}) or {}
    ).get("id")
    price_point_id = (
        rels.get("subscriptionPricePoint", {}).get("data", {}) or {}
    ).get("id")

    territory_a2: str | None = None
    if territory_id:
        # Apple territory IDs are alpha-3 (USA, GBR, etc.). If we ever
        # see one we don't have a mapping for, surface ``None`` rather
        # than leaking the alpha-3 — the schema contract is alpha-2.
        territory_a2 = ALPHA3_TO_ALPHA2.get(territory_id)

    # Apple sometimes omits offerMode/duration on list responses and can
    # send a non-numeric numberOfPeriods; coerce defensively so a read
    # never raises a 500 ResponseValidationError. Unrecognized enum
    # values fall through to ``None`` (the schema now allows it).
    offer_mode = attrs.get("offerMode")
    if offer_mode not in _INTRO_OFFER_MODES:
        offer_mode = None
    duration = attrs.get("duration")
    if duration not in _INTRO_OFFER_DURATIONS:
        duration = None
    try:
        number_of_periods = int(attrs.get("numberOfPeriods", 1))
    except (TypeError, ValueError):
        number_of_periods = 1

    return IntroOfferResponse(
        id=resource["id"],
        territory_code=territory_a2,
        offer_mode=offer_mode,
        duration=duration,
        number_of_periods=number_of_periods,
        price_point_id=price_point_id,
        start_date=attrs.get("startDate"),
        end_date=attrs.get("endDate"),
    )


def _normalize_locale(loc: str) -> str:
    """Reduce a locale tag to its primary subtag for matching.

    Apple stores some short codes in their canonical regional form
    (``th`` -> ``th-TH``, ``uk`` -> ``uk-UA``). Comparing on the primary
    subtag lets us recognise that a request for ``th`` already exists
    as ``th-TH`` and PATCH instead of POST.
    """
    return loc.split("-", 1)[0].lower()


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
    existing_by_exact: dict[str, dict] = {}
    existing_by_prefix: dict[str, dict] = {}
    for item in existing:
        locale = item.get("attributes", {}).get("locale", "")
        if not locale:
            continue
        existing_by_exact[locale] = item
        # Last writer wins for collisions (e.g. pt-PT vs pt-BR) — the
        # exact-match dict takes precedence in lookup, so prefix
        # collisions only matter when the requester sent a bare prefix.
        existing_by_prefix[_normalize_locale(locale)] = item

    created = 0
    updated = 0
    failed = 0
    errors: list[str] = []
    results: list[LocalizationResponse] = []
    matched_ids: set[str] = set()

    # Per-locale isolation: a single ASC failure (e.g. a REJECTED locale
    # that can't be PATCHed) must not abort the batch and discard the
    # record of locales that already succeeded.
    for loc in requested:
        existing_item = (
            existing_by_exact.get(loc.locale)
            or existing_by_prefix.get(_normalize_locale(loc.locale))
        )
        try:
            if existing_item is not None:
                resource = await update_fn(
                    existing_item["id"],
                    loc.name,
                    loc.description,
                )
                matched_ids.add(existing_item["id"])
                updated += 1
            else:
                resource = await create_fn(
                    loc.locale, loc.name, loc.description,
                )
                created += 1
        except ASCAPIError as exc:
            failed += 1
            errors.append(f"{loc.locale}: {exc.message}")
            if existing_item is not None and existing_item.get("id"):
                matched_ids.add(existing_item["id"])
            continue
        results.append(_parse_localization(resource.get("data", resource)))

    # Include untouched existing localizations in the response
    for item in existing:
        if item.get("id") and item["id"] not in matched_ids:
            results.append(_parse_localization(item))

    return BulkLocalizationResponse(
        created=created,
        updated=updated,
        localizations=results,
        failed=failed,
        errors=errors,
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
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_subscription_localization(
                subscription.asc_subscription_id, localization_id,
            )
            result = await pricing_service.update_subscription_localization(
                localization_id,
                body.name,
                body.description,
            )
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    return _parse_localization(result.get("data", result))


@router.delete(
    "/{app_id}/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a subscription.

    Apple permits deletion only when the sub is in DRAFT state and was
    never submitted. Used by the clone-cleanup flow to remove accidental
    version-bump shells with no prices and no review submission. Also
    removes the local cache row so the next ``GET .../subscriptions``
    sync reflects the new ASC state.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session,
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.delete_subscription(
                subscription.asc_subscription_id,
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )

    await session.delete(subscription)
    await session.flush()


@router.delete(
    "/{app_id}/subscriptions/{subscription_id}/localizations/{localization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription_localization(
    app_id: int,
    subscription_id: int,
    localization_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a subscription localization.

    Recovery path for localizations stuck in REJECTED state: Apple's PATCH
    is blocked there (409), but DELETE is allowed. After delete, the client
    should POST a fresh localization (which is born in PREPARE_FOR_SUBMISSION
    state and unblocks the parent subscription's Submit for Review button).
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Authorization: subscription must belong to this app.
    subscription = await _get_verified_subscription(
        subscription_id, app.id, session
    )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_subscription_localization(
                subscription.asc_subscription_id, localization_id,
            )
            await pricing_service.delete_subscription_localization(
                localization_id,
            )
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )


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

        try:
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
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)


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
    iap = await _get_verified_iap(iap_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_iap_localization(
                iap.asc_iap_id, localization_id,
            )
            result = await pricing_service.update_iap_localization(
                localization_id,
                body.name,
                body.description,
            )
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
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

        try:
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
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)


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


# ------------------------------------------------------------------
# Subscription group create / update
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscription-groups",
    response_model=SubscriptionGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription_group(
    app_id: int,
    body: SubscriptionGroupCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionGroupResponse:
    """Create a subscription group in ASC and mirror it to our DB."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_subscription_group(
                app.asc_app_id, body.reference_name
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    asc_group_id = result["data"]["id"]
    reference_name = (
        result["data"]
        .get("attributes", {})
        .get("referenceName", body.reference_name)
    )

    group = SubscriptionGroup(
        app_id=app.id,
        asc_group_id=asc_group_id,
        name=reference_name,
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)

    return SubscriptionGroupResponse.model_validate(group)


@router.patch(
    "/{app_id}/subscription-groups/{group_id}",
    response_model=SubscriptionGroupResponse,
)
async def update_subscription_group(
    app_id: int,
    group_id: int,
    body: SubscriptionGroupUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionGroupResponse:
    """Rename a subscription group."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.update_subscription_group(
                group.asc_group_id, body.reference_name
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    group.name = body.reference_name
    await session.commit()
    await session.refresh(group)

    return SubscriptionGroupResponse.model_validate(group)


# ------------------------------------------------------------------
# Subscription group localizations
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/subscription-groups/{group_id}/localizations",
    response_model=list[GroupLocalizationResponse],
)
async def list_subscription_group_localizations(
    app_id: int,
    group_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[GroupLocalizationResponse]:
    """List localizations for a subscription group."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            data = await pricing_service.list_subscription_group_localizations(
                group.asc_group_id
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return [_parse_group_localization(item) for item in data]


@router.post(
    "/{app_id}/subscription-groups/{group_id}/localizations",
    response_model=GroupLocalizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription_group_localization(
    app_id: int,
    group_id: int,
    body: GroupLocalizationCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GroupLocalizationResponse:
    """Create a subscriptionGroupLocalization."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_subscription_group_localization(
                group.asc_group_id,
                body.locale,
                body.name,
                body.custom_app_name,
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return _parse_group_localization(result.get("data", result))


@router.patch(
    "/{app_id}/subscription-groups/{group_id}/localizations/{localization_id}",
    response_model=GroupLocalizationResponse,
)
async def update_subscription_group_localization(
    app_id: int,
    group_id: int,
    localization_id: str,
    body: GroupLocalizationUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GroupLocalizationResponse:
    """Update a subscriptionGroupLocalization (locale is immutable)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Authorization: the group must belong to this app.
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_subscription_group_localization(
                group.asc_group_id, localization_id,
            )
            result = await pricing_service.update_subscription_group_localization(
                localization_id, body.name, body.custom_app_name
            )
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return _parse_group_localization(result.get("data", result))


@router.delete(
    "/{app_id}/subscription-groups/{group_id}/localizations/{localization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription_group_localization(
    app_id: int,
    group_id: int,
    localization_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a subscriptionGroupLocalization.

    Recovery path mirroring ``delete_subscription_localization``: Apple's
    PATCH on group localizations works in REJECTED state, but state
    itself does not transition. To move a group localization out of
    REJECTED you must DELETE + re-CREATE; the new row is born in
    PREPARE_FOR_SUBMISSION.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_subscription_group_localization(
                group.asc_group_id, localization_id,
            )
            await pricing_service.delete_subscription_group_localization(
                localization_id,
            )
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            )
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            )


# ------------------------------------------------------------------
# Subscription create / update
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/subscription-groups/{group_id}/subscriptions",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    app_id: int,
    group_id: int,
    body: SubscriptionCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionResponse:
    """Create a new auto-renewable subscription within a group."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    group = await _get_verified_subscription_group(group_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_subscription(
                group_id=group.asc_group_id,
                product_id=body.product_id,
                name=body.name,
                period=body.period,
                family_sharable=body.family_sharable,
                available_in_all_territories=body.available_in_all_territories,
                group_level=body.group_level,
                review_note=body.review_note,
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

        asc_sub_id = result["data"]["id"]

        # Apple ships new subs with zero territories enabled. Without this
        # follow-up POST, every subsequent prices/apply fails with the
        # generic "An error occurred while processing the pricing
        # information" message. Source the territory list from the app's
        # own availability (canonical Apple-recognized set) — never from
        # our 203-entry alpha-2 map, which Apple silently truncates and
        # has previously dropped HKG, blocking subs at MISSING_METADATA.
        if body.available_in_all_territories:
            try:
                alpha3, avail_in_new = await _resolve_app_target_territories(
                    client, app.asc_app_id
                )
                await pricing_service.create_subscription_availability(
                    subscription_id=asc_sub_id,
                    available_alpha3_codes=alpha3,
                    available_in_new_territories=avail_in_new,
                )
            except ASCAPIError as exc:
                # The sub already exists in ASC at this point; surface the
                # availability failure but don't roll back the create.
                logger.warning(
                    "Subscription %s created but availability POST failed "
                    "(%d): %s — apply will fail until availability is set "
                    "manually",
                    asc_sub_id, exc.status_code, exc.message,
                )

    attrs = result["data"].get("attributes", {})

    sub = Subscription(
        group_id=group.id,
        asc_subscription_id=asc_sub_id,
        name=attrs.get("name", body.name),
        product_id=attrs.get("productId", body.product_id),
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)

    return SubscriptionResponse.model_validate(sub)


@router.patch(
    "/{app_id}/subscriptions/{subscription_id}",
    response_model=SubscriptionResponse,
)
async def update_subscription(
    app_id: int,
    subscription_id: int,
    body: SubscriptionUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionResponse:
    """Update editable subscription metadata.

    productId and subscriptionPeriod are immutable in ASC and rejected
    by the schema.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    sub = await _get_verified_subscription(subscription_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.update_subscription(
                sub.asc_subscription_id,
                name=body.name,
                group_level=body.group_level,
                family_sharable=body.family_sharable,
                review_note=body.review_note,
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    if body.name is not None:
        sub.name = body.name
        await session.commit()
        await session.refresh(sub)

    return SubscriptionResponse.model_validate(sub)


# ------------------------------------------------------------------
# Introductory offers
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/subscriptions/{subscription_id}/intro-offers",
    response_model=list[IntroOfferResponse],
)
async def list_subscription_intro_offers(
    app_id: int,
    subscription_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[IntroOfferResponse]:
    """List introductory offers for a subscription."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    sub = await _get_verified_subscription(subscription_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            data = await pricing_service.list_subscription_introductory_offers(
                sub.asc_subscription_id
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return [_parse_intro_offer(item) for item in data]


@router.post(
    "/{app_id}/subscriptions/{subscription_id}/intro-offers",
    response_model=IntroOfferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription_intro_offer(
    app_id: int,
    subscription_id: int,
    body: IntroOfferCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IntroOfferResponse:
    """Create an introductory offer for a subscription."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    sub = await _get_verified_subscription(subscription_id, app.id, session)

    territory_id = ALPHA2_TO_ALPHA3.get(body.territory_code)
    if territory_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown territory_code: {body.territory_code}",
        )

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            result = await pricing_service.create_subscription_introductory_offer(
                subscription_id=sub.asc_subscription_id,
                offer_mode=body.offer_mode,
                duration=body.duration,
                number_of_periods=body.number_of_periods,
                territory_id=territory_id,
                price_point_id=body.price_point_id,
                start_date=body.start_date.isoformat() if body.start_date else None,
                end_date=body.end_date.isoformat() if body.end_date else None,
            )
        except ASCAPIError as exc:
            logger.warning(
                "Failed to create intro offer for subscription %s "
                "(status %d): %s | request=%s | apple_body=%s",
                subscription_id,
                exc.status_code,
                exc.message,
                {
                    "territory_code": body.territory_code,
                    "territory_id": territory_id,
                    "offer_mode": body.offer_mode,
                    "duration": body.duration,
                    "number_of_periods": body.number_of_periods,
                    "price_point_id": body.price_point_id,
                },
                exc.response_body,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return _parse_intro_offer(
        {"resource": result.get("data", result), "included": []}
    )


@router.delete(
    "/{app_id}/subscriptions/{subscription_id}/intro-offers/{offer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription_intro_offer(
    app_id: int,
    subscription_id: int,
    offer_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an introductory offer."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Authorization: the subscription must belong to this app.
    sub = await _get_verified_subscription(subscription_id, app.id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        pricing_service = ASCPricingService(client)
        try:
            await pricing_service.assert_subscription_intro_offer(
                sub.asc_subscription_id, offer_id,
            )
            await pricing_service.delete_subscription_introductory_offer(offer_id)
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            )
        except ASCAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
