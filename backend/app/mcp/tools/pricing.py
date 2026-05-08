"""MCP tools for the pricing surface.

Thin wrappers over the existing REST handlers in ``app/api/v1/pricing.py``
and ``app/api/v1/export.py``. Each tool re-uses the same helpers
(``_get_verified_app``, ``_get_verified_subscription``, etc.) and the same
service layer so business logic stays in one place. Where REST returns
binary (xlsx/csv), the MCP tools accept/return base64-encoded payloads so
they cross the JSON-only MCP transport.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_asc_client_for_app
from app.api.v1.pricing import (
    ALPHA3_TO_ALPHA2,
    SAFETY_LABEL,
    SAFETY_MAX_DOWN,
    SAFETY_MAX_UP,
    _apply_charming,
    _build_preview_item,
    _bulk_sync_localizations,
    _get_territory_map,
    _get_verified_iap as _http_get_verified_iap,
    _get_verified_subscription as _http_get_verified_subscription,
    _get_verified_subscription_group as _http_get_verified_subscription_group,
    _parse_group_localization,
    _parse_intro_offer,
    _parse_localization,
    _parse_screenshot,
    _resolve_app_target_territories,
    _unique_territories,
)
from app.data.territories import ALPHA2_TO_ALPHA3
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.economic_index import EconomicIndex
from app.models.iap import IAPPrice, InAppPurchase
from app.models.subscription import (
    Subscription,
    SubscriptionGroup,
    SubscriptionPrice,
)
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
from app.services.asc.errors import ASCAPIError
from app.services.asc.price_point_cache import PricePointCache
from app.services.asc.pricing import ASCPricingService
from app.services.export.csv import CSVExportService
from app.services.export.excel import ExcelExportService
from app.services.pricing.currency import effective_currency

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _asc_error(exc: ASCAPIError) -> ToolError:
    """Normalize an ASCAPIError into a ToolError preserving the message."""
    return ToolError(f"ASC error ({exc.status_code}): {exc.message}")


# Wrappers around the REST router helpers that raise HTTPException; the MCP
# transport never speaks HTTP status codes, so we surface only the ``detail``.


async def _get_verified_subscription(subscription_id, app_id, session):
    try:
        return await _http_get_verified_subscription(subscription_id, app_id, session)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


async def _get_verified_iap(iap_id, app_id, session):
    try:
        return await _http_get_verified_iap(iap_id, app_id, session)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


async def _get_verified_subscription_group(group_id, app_id, session):
    try:
        return await _http_get_verified_subscription_group(group_id, app_id, session)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc



def _safety_skip_item(
    territory_code: str,
    *,
    current_price: float | None,
    new_price: float,
    force: bool,
) -> PriceApplySkippedItem | None:
    """Return a skip record if the new price exceeds the ±50% safety band, else None."""
    if force or current_price is None or current_price <= 0:
        return None
    if not (
        new_price > current_price * SAFETY_MAX_UP
        or new_price < current_price * SAFETY_MAX_DOWN
    ):
        return None
    diff_pct = round(((new_price - current_price) / current_price) * 100, 2)
    return PriceApplySkippedItem(
        territory_code=territory_code,
        reason=(
            f"Price change {diff_pct:+}% exceeds safety limit ({SAFETY_LABEL})"
        ),
        current_price=current_price,
        new_price=new_price,
        diff_percent=diff_pct,
    )


# ==================================================================
# Subscription groups
# ==================================================================


@mcp.tool(name="pricing.list_subscription_groups")
async def list_subscription_groups(
    app_id: int,
) -> list[SubscriptionGroupWithSubscriptionsResponse]:
    """List subscription groups (and their subs) for an app — syncs from ASC."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                groups_data = await pricing_service.list_subscription_groups(
                    app.asc_app_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

            for group_data in groups_data:
                asc_group_id = group_data["id"]
                group_name = group_data.get("attributes", {}).get(
                    "referenceName", "Unknown"
                )

                existing = await session.execute(
                    select(SubscriptionGroup).where(
                        SubscriptionGroup.app_id == app.id,
                        SubscriptionGroup.asc_group_id == asc_group_id,
                    )
                )
                group_record = existing.scalar_one_or_none()

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

                try:
                    subs_data = await pricing_service.list_subscriptions(
                        asc_group_id
                    )
                except ASCAPIError as exc:
                    raise _asc_error(exc)

                for sub_data in subs_data:
                    asc_sub_id = sub_data["id"]
                    attrs = sub_data.get("attributes", {})

                    sub_existing = await session.execute(
                        select(Subscription).where(
                            Subscription.group_id == group_record.id,
                            Subscription.asc_subscription_id == asc_sub_id,
                        )
                    )
                    sub_record = sub_existing.scalar_one_or_none()
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

        result = await session.execute(
            select(SubscriptionGroup)
            .options(selectinload(SubscriptionGroup.subscriptions))
            .where(SubscriptionGroup.app_id == app.id)
        )
        return [
            SubscriptionGroupWithSubscriptionsResponse.model_validate(g)
            for g in result.scalars().all()
        ]


@mcp.tool(name="pricing.create_subscription_group")
async def create_subscription_group(
    app_id: int, reference_name: str
) -> SubscriptionGroupResponse:
    """Create a new subscription group in ASC (and mirror to DB)."""
    body = SubscriptionGroupCreate(reference_name=reference_name)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.create_subscription_group(
                    app.asc_app_id, body.reference_name
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        asc_group_id = result["data"]["id"]
        ref_name = (
            result["data"]
            .get("attributes", {})
            .get("referenceName", body.reference_name)
        )
        group = SubscriptionGroup(
            app_id=app.id, asc_group_id=asc_group_id, name=ref_name,
        )
        session.add(group)
        await session.flush()
        await session.refresh(group)
        return SubscriptionGroupResponse.model_validate(group)


@mcp.tool(name="pricing.update_subscription_group")
async def update_subscription_group(
    app_id: int, group_id: int, reference_name: str,
) -> SubscriptionGroupResponse:
    """Rename a subscription group in ASC."""
    body = SubscriptionGroupUpdate(reference_name=reference_name)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        group = await _get_verified_subscription_group(group_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.update_subscription_group(
                    group.asc_group_id, body.reference_name
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        group.name = body.reference_name
        await session.flush()
        await session.refresh(group)
        return SubscriptionGroupResponse.model_validate(group)


# ==================================================================
# Subscription group localizations
# ==================================================================


@mcp.tool(name="pricing.list_subscription_group_localizations")
async def list_subscription_group_localizations(
    app_id: int, group_id: int,
) -> list[GroupLocalizationResponse]:
    """List localizations attached to a subscription group."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        group = await _get_verified_subscription_group(group_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                data = await pricing_service.list_subscription_group_localizations(
                    group.asc_group_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        return [_parse_group_localization(item) for item in data]


@mcp.tool(name="pricing.create_subscription_group_localization")
async def create_subscription_group_localization(
    app_id: int,
    group_id: int,
    locale: str,
    name: str,
    custom_app_name: str | None = None,
) -> GroupLocalizationResponse:
    """Create a subscriptionGroupLocalization."""
    body = GroupLocalizationCreate(
        locale=locale, name=name, custom_app_name=custom_app_name,
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)

        return _parse_group_localization(result.get("data", result))


@mcp.tool(name="pricing.update_subscription_group_localization")
async def update_subscription_group_localization(
    app_id: int,
    group_id: int,
    localization_id: str,
    name: str,
    custom_app_name: str | None = None,
) -> GroupLocalizationResponse:
    """Update a subscriptionGroupLocalization (locale immutable)."""
    body = GroupLocalizationUpdate(name=name, custom_app_name=custom_app_name)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription_group(group_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.update_subscription_group_localization(
                    localization_id, body.name, body.custom_app_name,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        return _parse_group_localization(result.get("data", result))


@mcp.tool(name="pricing.delete_subscription_group_localization")
async def delete_subscription_group_localization(
    app_id: int, group_id: int, localization_id: str,
) -> dict[str, bool]:
    """Delete a subscriptionGroupLocalization."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription_group(group_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.delete_subscription_group_localization(
                    localization_id,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)
        return {"deleted": True}


# ==================================================================
# Subscriptions (write paths)
# ==================================================================


@mcp.tool(name="pricing.create_subscription")
async def create_subscription(
    app_id: int,
    group_id: int,
    product_id: str,
    name: str,
    period: str,
    family_sharable: bool = False,
    available_in_all_territories: bool = True,
    group_level: int = 1,
    review_note: str | None = None,
) -> SubscriptionResponse:
    """Create an auto-renewable subscription within a group."""
    body = SubscriptionCreate(
        product_id=product_id,
        name=name,
        period=period,  # type: ignore[arg-type]
        family_sharable=family_sharable,
        available_in_all_territories=available_in_all_territories,
        group_level=group_level,
        review_note=review_note,
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)

            asc_sub_id = result["data"]["id"]

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
                    logger.warning(
                        "Subscription %s created but availability POST failed "
                        "(%d): %s",
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
        await session.flush()
        await session.refresh(sub)
        return SubscriptionResponse.model_validate(sub)


@mcp.tool(name="pricing.update_subscription")
async def update_subscription(
    app_id: int,
    subscription_id: int,
    name: str | None = None,
    group_level: int | None = None,
    family_sharable: bool | None = None,
    review_note: str | None = None,
) -> SubscriptionResponse:
    """Update editable subscription metadata (productId/period are immutable)."""
    body = SubscriptionUpdate(
        name=name,
        group_level=group_level,
        family_sharable=family_sharable,
        review_note=review_note,
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)

        if body.name is not None:
            sub.name = body.name
            await session.flush()
            await session.refresh(sub)
        return SubscriptionResponse.model_validate(sub)


@mcp.tool(name="pricing.delete_subscription")
async def delete_subscription(
    app_id: int, subscription_id: int,
) -> dict[str, bool]:
    """Delete a DRAFT subscription (Apple only allows delete in DRAFT)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        sub = await _get_verified_subscription(subscription_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.delete_subscription(
                    sub.asc_subscription_id,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        await session.delete(sub)
        await session.flush()
        return {"deleted": True}


@mcp.tool(name="pricing.get_subscription_availability")
async def get_subscription_availability(
    app_id: int, subscription_id: int,
) -> SubscriptionAvailabilityResponse:
    """Return alpha-2 codes of territories the subscription is available in."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)

        territories = sorted(
            ALPHA3_TO_ALPHA2[code] for code in alpha3 if code in ALPHA3_TO_ALPHA2
        )
        return SubscriptionAvailabilityResponse(
            subscription_id=subscription.id,
            territories=territories,
        )


# ==================================================================
# Subscription localizations
# ==================================================================


@mcp.tool(name="pricing.list_subscription_localizations")
async def list_subscription_localizations(
    app_id: int, subscription_id: int,
) -> list[LocalizationResponse]:
    """Fetch all localizations for a subscription from ASC."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                data = await pricing_service.list_subscription_localizations(
                    subscription.asc_subscription_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        return [_parse_localization(item) for item in data]


@mcp.tool(name="pricing.create_subscription_localization")
async def create_subscription_localization(
    app_id: int,
    subscription_id: int,
    locale: str,
    name: str,
    description: str,
) -> LocalizationResponse:
    """Create a subscription localization."""
    body = LocalizationCreate(locale=locale, name=name, description=description)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)

        return _parse_localization(result.get("data", result))


@mcp.tool(name="pricing.update_subscription_localization")
async def update_subscription_localization(
    app_id: int,
    subscription_id: int,
    localization_id: str,
    name: str,
    description: str,
) -> LocalizationResponse:
    """Update a subscription localization (locale immutable)."""
    body = LocalizationUpdate(name=name, description=description)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription(subscription_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.update_subscription_localization(
                    localization_id, body.name, body.description,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        return _parse_localization(result.get("data", result))


@mcp.tool(name="pricing.delete_subscription_localization")
async def delete_subscription_localization(
    app_id: int, subscription_id: int, localization_id: str,
) -> dict[str, bool]:
    """Delete a subscription localization (recovery for REJECTED state)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription(subscription_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.delete_subscription_localization(
                    localization_id,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)
        return {"deleted": True}


@mcp.tool(name="pricing.bulk_sync_subscription_localizations")
async def bulk_sync_subscription_localizations(
    app_id: int,
    subscription_id: int,
    localizations: list[dict[str, str]],
) -> BulkLocalizationResponse:
    """Bulk create/update subscription localizations (update if exists)."""
    body = BulkLocalizationRequest(
        localizations=[LocalizationCreate(**loc) for loc in localizations],
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                            asc_sub_id, locale, name, desc,
                        )
                    ),
                    update_fn=lambda loc_id, name, desc: (
                        pricing_service.update_subscription_localization(
                            loc_id, name, desc,
                        )
                    ),
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)


# ==================================================================
# Subscription prices: read / sync / preview / resolve / apply
# ==================================================================


@mcp.tool(name="pricing.get_subscription_prices")
async def get_subscription_prices(
    app_id: int, subscription_id: int,
) -> SubscriptionPricesResponse:
    """Return cached prices for a subscription from DB (no ASC call)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

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


@mcp.tool(name="pricing.sync_subscription_prices")
async def sync_subscription_prices(
    app_id: int, subscription_id: int,
) -> SyncPricesResponse:
    """Sync current prices from ASC into DB cache (~175 territories)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )
        territory_map = await _get_territory_map(session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                prices_data = await pricing_service.get_subscription_prices(
                    subscription.asc_subscription_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

            now = datetime.now(timezone.utc)
            prices_synced = 0

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
                    price_record.customer_price = price_item.get(
                        "customer_price", 0.0
                    )
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

        return SyncPricesResponse(
            prices_synced=prices_synced, price_points_synced=0,
        )


@mcp.tool(name="pricing.subscription_price_points_status")
async def subscription_price_points_status(
    app_id: int, subscription_id: int,
) -> PricePointCacheStatus:
    """Return the status of the (subscription) price-point filesystem cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription(subscription_id, app.id, session)

        cache = PricePointCache()
        info = await cache.status()
        return PricePointCacheStatus(**info)


@mcp.tool(name="pricing.sync_subscription_price_points")
async def sync_subscription_price_points(
    app_id: int, subscription_id: int,
) -> PricePointSyncResponse:
    """Sync Apple subscription price tiers into the app-wide filesystem cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )
        territory_map = await _get_territory_map(session)
        territory_by_id = {t.id: t for t in territory_map.values()}
        territory_codes = sorted({t.code for t in territory_by_id.values()})

        cache = PricePointCache()
        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            total_points = await cache.fetch_and_cache_all(
                territory_codes,
                subscription.asc_subscription_id,
                pricing_service,
            )

        return PricePointSyncResponse(
            territories_synced=len(territory_codes),
            price_points_total=total_points,
        )


def _preview_via_index(
    base_price: float,
    base_index_value: float,
    territories: list,
    index_by_territory: dict[int, float],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    charming_mode: str,
) -> list[PricePreviewItem]:
    items: list[PricePreviewItem] = []
    for territory in territories:
        territory_index = index_by_territory.get(territory.id)
        if territory_index is None:
            continue
        currency = effective_currency(
            territory, price_points_by_territory.get(territory.code),
        )
        suggested = base_price * (territory_index / base_index_value)
        suggested = _apply_charming(suggested, charming_mode, currency)
        items.append(_build_preview_item(
            territory=territory,
            currency_code=currency,
            suggested=suggested,
            current_price_by_territory=current_price_by_territory,
            price_points_by_territory=price_points_by_territory,
        ))
    return items


def _finalize_suggested(
    raw_decimal: Any,
    *,
    territory: Any,
    currency: str,
    apply_vat_flag: bool,
    charming_mode: str,
) -> float:
    """Apply optional VAT and either smart rounding or _apply_charming."""
    from app.services.pricing.currency_rounding import apply_currency_rounding
    from app.services.pricing.vat import apply_vat

    if apply_vat_flag and territory.vat_rate and territory.vat_rate > 0:
        raw_decimal = apply_vat(raw_decimal, territory.vat_rate)
    if charming_mode == "smart":
        return float(apply_currency_rounding(raw_decimal, currency))
    return _apply_charming(float(raw_decimal), charming_mode, currency)


async def _preview_via_exchange_rate(
    body: PricePreviewRequest,
    territory_map: dict,
    all_territories: list,
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
) -> list[PricePreviewItem]:
    """Compute preview items using direct FX rates from the rate-cache API."""
    from decimal import Decimal

    from app.core.config import settings
    from app.services.rates import RateCacheClient, RateCacheError

    base_territory = territory_map.get(body.base_territory_code)
    if base_territory is None:
        raise ToolError(
            f"Base territory '{body.base_territory_code}' not found"
        )
    base_currency = effective_currency(
        base_territory,
        price_points_by_territory.get(base_territory.code),
    )
    try:
        rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
        rates = await rate_client.get_rates(base=base_currency)
    except RateCacheError as exc:
        raise ToolError(f"Failed to fetch exchange rates: {exc}")

    items: list[PricePreviewItem] = []
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
        raw = Decimal(str(body.base_price)) * Decimal(str(rate))
        suggested = _finalize_suggested(
            raw,
            territory=territory,
            currency=currency,
            apply_vat_flag=body.apply_vat,
            charming_mode=body.charming_mode,
        )
        items.append(_build_preview_item(
            territory=territory,
            currency_code=currency,
            suggested=suggested,
            current_price_by_territory=current_price_by_territory,
            price_points_by_territory=price_points_by_territory,
        ))
    return items


async def _preview_via_gdp_brackets(
    body: PricePreviewRequest,
    session,
    all_territories: list,
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
) -> list[PricePreviewItem]:
    """Compute preview items using GDP brackets + USD-denominated tier prices."""
    from decimal import Decimal

    from app.core.config import settings
    from app.services.pricing.gdp_brackets import assign_tier
    from app.services.rates import RateCacheClient, RateCacheError

    assert body.gdp_config is not None
    gdp_indices_result = await session.execute(
        select(EconomicIndex).where(
            EconomicIndex.index_type == "gdp_per_capita_ppp"
        )
    )
    gdp_by_territory_id = {
        idx.territory_id: idx.value
        for idx in gdp_indices_result.scalars().all()
    }
    try:
        rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
        rates = await rate_client.get_rates(base="USD")
    except RateCacheError as exc:
        raise ToolError(f"Failed to fetch exchange rates: {exc}")

    items: list[PricePreviewItem] = []
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
        raw = tier_price_usd * rate
        suggested = _finalize_suggested(
            raw,
            territory=territory,
            currency=currency,
            apply_vat_flag=body.apply_vat,
            charming_mode=body.charming_mode,
        )
        items.append(_build_preview_item(
            territory=territory,
            currency_code=currency,
            suggested=suggested,
            current_price_by_territory=current_price_by_territory,
            price_points_by_territory=price_points_by_territory,
        ))
    return items


async def _preview_items(
    body: PricePreviewRequest,
    session,
    territory_map: dict,
    all_territories: list,
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
) -> list[PricePreviewItem]:
    """Dispatch to the right preview branch based on ``index_type``."""
    if body.index_type == "exchange_rate":
        return await _preview_via_exchange_rate(
            body=body,
            territory_map=territory_map,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
        )
    if body.index_type == "gdp_brackets":
        return await _preview_via_gdp_brackets(
            body=body,
            session=session,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
        )
    indices_result = await session.execute(
        select(EconomicIndex).where(
            EconomicIndex.index_type == body.index_type
        )
    )
    index_by_territory = {
        idx.territory_id: idx.value
        for idx in indices_result.scalars().all()
    }
    base_territory = territory_map.get(body.base_territory_code)
    if base_territory is None:
        raise ToolError(
            f"Base territory '{body.base_territory_code}' not found"
        )
    base_index_value = index_by_territory.get(base_territory.id)
    if base_index_value is None or base_index_value == 0:
        raise ToolError(
            f"No {body.index_type} index data for territory "
            f"'{body.base_territory_code}'"
        )
    return _preview_via_index(
        base_price=body.base_price,
        base_index_value=base_index_value,
        territories=all_territories,
        index_by_territory=index_by_territory,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        charming_mode=body.charming_mode,
    )


@mcp.tool(name="pricing.preview_subscription_prices")
async def preview_subscription_prices(
    app_id: int,
    subscription_id: int,
    request: dict[str, Any],
) -> PricePreviewResponse:
    """Preview suggested subscription prices.

    ``request`` is a :class:`PricePreviewRequest` payload (index_type,
    base_price, base_territory_code, apply_vat, charming_mode, gdp_config).
    """
    body = PricePreviewRequest.model_validate(request)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )
        territory_map = await _get_territory_map(session)

        current_prices_result = await session.execute(
            select(SubscriptionPrice).where(
                SubscriptionPrice.subscription_id == subscription.id
            )
        )
        current_price_by_territory = {
            p.territory_id: p for p in current_prices_result.scalars().all()
        }

        cache = PricePointCache()
        price_points_by_territory: dict[str, list[dict]] = {}
        all_territories = _unique_territories(territory_map)
        for territory in all_territories:
            cached = await cache.get_with_price_point_ids(
                territory.code, subscription.asc_subscription_id,
            )
            if cached is not None:
                price_points_by_territory[territory.code] = cached

        preview_items = await _preview_items(
            body=body,
            session=session,
            territory_map=territory_map,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
        )

        return PricePreviewResponse(
            subscription_id=subscription.id,
            subscription_name=subscription.name,
            index_type=body.index_type,
            base_price=body.base_price,
            items=preview_items,
        )


@mcp.tool(name="pricing.resolve_subscription_price")
async def resolve_subscription_price(
    app_id: int,
    subscription_id: int,
    territory_code: str,
    price: float,
) -> PriceResolveResponse:
    """Resolve a manual price to the nearest Apple price tier (cached)."""
    body = PriceResolveRequest(territory_code=territory_code, price=price)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

        cache = PricePointCache()
        pps = await cache.get_with_price_point_ids(
            body.territory_code, subscription.asc_subscription_id,
        )
        if not pps:
            raise ToolError(
                f"No cached price tiers for territory '{body.territory_code}'. "
                f"Sync price tiers first."
            )
        nearest = min(pps, key=lambda pp: abs(pp["customer_price"] - body.price))
        return PriceResolveResponse(
            territory_code=body.territory_code,
            currency_code=nearest["currency_code"],
            customer_price=nearest["customer_price"],
            proceeds=nearest["proceeds"],
            price_point_id=nearest["price_point_id"],
        )


@mcp.tool(name="pricing.apply_subscription_prices")
async def apply_subscription_prices(
    app_id: int,
    subscription_id: int,
    request: dict[str, Any],
) -> PriceApplyResponse:
    """Apply subscription prices via ASC (with ±50% safety band).

    ``request`` is a :class:`PriceApplyRequest` payload: ``items`` is a list of
    ``{territory_code, price_point_id, force?}`` and an optional
    ``intro_offer`` ``{duration, number_of_periods}``.
    """
    body = PriceApplyRequest.model_validate(request)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

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

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)

            for item in body.items:
                tc = item.territory_code
                current_price = current_price_by_code.get(tc)

                territory_pps = await cache.get_with_price_point_ids(
                    tc, subscription.asc_subscription_id,
                )
                if territory_pps is None:
                    try:
                        await cache.fetch_and_cache(
                            tc, subscription.asc_subscription_id, pricing_service,
                        )
                        territory_pps = await cache.get_with_price_point_ids(
                            tc, subscription.asc_subscription_id,
                        ) or []
                    except Exception:
                        failed += 1
                        errors.append(
                            f"Territory {tc}: failed to fetch price tiers"
                        )
                        continue

                new_price: float | None = None
                for pp in territory_pps:
                    if pp["price_point_id"] == item.price_point_id:
                        new_price = pp["customer_price"]
                        break

                if new_price is None:
                    failed += 1
                    errors.append(
                        f"Territory {tc}: price_point_id "
                        f"{item.price_point_id!r} not found in cached tiers"
                    )
                    continue

                skip = _safety_skip_item(
                    tc,
                    current_price=current_price,
                    new_price=new_price,
                    force=item.force,
                )
                if skip is not None:
                    skipped += 1
                    skipped_items.append(skip)
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
                    errors.append(f"Territory {tc}: {exc.message}")

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
                    existing_offers = []

                if intro_offer_error is None:
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

                    existing_by_alpha2: dict[str, dict] = {}
                    for item_dict in existing_offers:
                        resource = item_dict.get("resource") or {}
                        offer_id = resource.get("id")
                        attrs = resource.get("attributes") or {}
                        rel = (resource.get("relationships") or {}).get(
                            "territory"
                        ) or {}
                        terr_alpha3 = (rel.get("data") or {}).get("id")
                        terr_alpha2 = (
                            ALPHA3_TO_ALPHA2.get(terr_alpha3)
                            if terr_alpha3 else None
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
                            except ASCAPIError:
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
                            except ASCAPIError:
                                return False

                    if to_delete_ids:
                        delete_results = await asyncio.gather(
                            *[_delete_one(oid) for oid in to_delete_ids],
                            return_exceptions=False,
                        )
                        intro_offer_deleted = sum(
                            1 for ok in delete_results if ok
                        )
                    if to_create_alpha2:
                        create_results = await asyncio.gather(
                            *[_create_one(c) for c in to_create_alpha2],
                            return_exceptions=False,
                        )
                        intro_offer_applied = sum(
                            1 for ok in create_results if ok
                        )
                        intro_offer_failed = (
                            len(create_results) - intro_offer_applied
                        )

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

        return PriceApplyResponse(
            applied=applied,
            failed=failed,
            skipped=skipped,
            errors=errors,
            skipped_items=skipped_items,
            intro_offer_synced=intro_offer_synced,
            intro_offer_error=intro_offer_error,
        )


# ==================================================================
# Subscription intro offers
# ==================================================================


@mcp.tool(name="pricing.list_subscription_intro_offers")
async def list_subscription_intro_offers(
    app_id: int, subscription_id: int,
) -> list[IntroOfferResponse]:
    """List introductory offers for a subscription."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        sub = await _get_verified_subscription(subscription_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                data = await pricing_service.list_subscription_introductory_offers(
                    sub.asc_subscription_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        return [_parse_intro_offer(item) for item in data]


@mcp.tool(name="pricing.create_subscription_intro_offer")
async def create_subscription_intro_offer(
    app_id: int,
    subscription_id: int,
    request: dict[str, Any],
) -> IntroOfferResponse:
    """Create an introductory offer (per-territory). ``request`` matches IntroOfferCreate."""
    body = IntroOfferCreate.model_validate(request)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        sub = await _get_verified_subscription(subscription_id, app.id, session)

        territory_id = ALPHA2_TO_ALPHA3.get(body.territory_code)
        if territory_id is None:
            raise ToolError(f"Unknown territory_code: {body.territory_code}")

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
                raise _asc_error(exc)

        return _parse_intro_offer(
            {"resource": result.get("data", result), "included": []}
        )


@mcp.tool(name="pricing.delete_subscription_intro_offer")
async def delete_subscription_intro_offer(
    app_id: int, subscription_id: int, offer_id: str,
) -> dict[str, bool]:
    """Delete a subscription introductory offer."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_subscription(subscription_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                await pricing_service.delete_subscription_introductory_offer(
                    offer_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)
        return {"deleted": True}


# ==================================================================
# Review screenshots (subscription)
# ==================================================================


@mcp.tool(name="pricing.get_subscription_review_screenshot")
async def get_subscription_review_screenshot(
    app_id: int, subscription_id: int,
) -> ReviewScreenshotResponse | None:
    """Get the current review screenshot for a subscription, or None."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            data = await pricing_service.get_subscription_review_screenshot(
                subscription.asc_subscription_id
            )
        return _parse_screenshot(data)


@mcp.tool(name="pricing.upload_subscription_review_screenshot")
async def upload_subscription_review_screenshot(
    app_id: int,
    subscription_id: int,
    file_name: str,
    file_base64: str,
) -> ReviewScreenshotResponse:
    """Upload a review screenshot for a subscription. ``file_base64`` is the binary."""
    try:
        file_bytes = base64.b64decode(file_base64)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"file_base64 is not valid base64: {exc}")
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        subscription = await _get_verified_subscription(
            subscription_id, app.id, session
        )

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.upload_subscription_review_screenshot(
                    subscription.asc_subscription_id,
                    file_name or "screenshot.png",
                    file_bytes,
                )
            except ASCAPIError as exc:
                raise ToolError(f"Screenshot upload failed: {exc.message}")
        parsed = _parse_screenshot(result.get("data"))
        if parsed is None:
            raise ToolError("Screenshot upload succeeded but ASC returned no data")
        return parsed


# ==================================================================
# IAPs
# ==================================================================


@mcp.tool(name="pricing.list_iaps")
async def list_iaps(app_id: int) -> list[IAPResponse]:
    """Fetch in-app purchases from ASC and sync to DB."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                iaps_data = await pricing_service.list_iaps(app.asc_app_id)
            except ASCAPIError as exc:
                raise _asc_error(exc)

        for iap_data in iaps_data:
            asc_iap_id = iap_data["id"]
            attrs = iap_data.get("attributes", {})
            existing = await session.execute(
                select(InAppPurchase).where(
                    InAppPurchase.app_id == app.id,
                    InAppPurchase.asc_iap_id == asc_iap_id,
                )
            )
            iap_record = existing.scalar_one_or_none()
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

        result = await session.execute(
            select(InAppPurchase).where(InAppPurchase.app_id == app.id)
        )
        return [IAPResponse.model_validate(iap) for iap in result.scalars().all()]


@mcp.tool(name="pricing.list_iap_localizations")
async def list_iap_localizations(
    app_id: int, iap_id: int,
) -> list[LocalizationResponse]:
    """Fetch all localizations for an IAP from ASC."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                data = await pricing_service.list_iap_localizations(
                    iap.asc_iap_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)
        return [_parse_localization(item) for item in data]


@mcp.tool(name="pricing.create_iap_localization")
async def create_iap_localization(
    app_id: int,
    iap_id: int,
    locale: str,
    name: str,
    description: str,
) -> LocalizationResponse:
    """Create an IAP localization."""
    body = LocalizationCreate(locale=locale, name=name, description=description)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                raise _asc_error(exc)
        return _parse_localization(result.get("data", result))


@mcp.tool(name="pricing.update_iap_localization")
async def update_iap_localization(
    app_id: int,
    iap_id: int,
    localization_id: str,
    name: str,
    description: str,
) -> LocalizationResponse:
    """Update an IAP localization (locale immutable)."""
    body = LocalizationUpdate(name=name, description=description)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_iap(iap_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.update_iap_localization(
                    localization_id, body.name, body.description,
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)
        return _parse_localization(result.get("data", result))


@mcp.tool(name="pricing.bulk_sync_iap_localizations")
async def bulk_sync_iap_localizations(
    app_id: int,
    iap_id: int,
    localizations: list[dict[str, str]],
) -> BulkLocalizationResponse:
    """Bulk create/update IAP localizations."""
    body = BulkLocalizationRequest(
        localizations=[LocalizationCreate(**loc) for loc in localizations],
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
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
                            asc_iap_id, locale, name, desc,
                        )
                    ),
                    update_fn=lambda loc_id, name, desc: (
                        pricing_service.update_iap_localization(
                            loc_id, name, desc,
                        )
                    ),
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)


@mcp.tool(name="pricing.get_iap_prices")
async def get_iap_prices(app_id: int, iap_id: int) -> IAPPricesResponse:
    """Fetch current prices for an IAP from ASC, sync to DB."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)
        territory_map = await _get_territory_map(session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                prices_data = await pricing_service.get_iap_price_schedule(
                    iap.asc_iap_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

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
            currency_code = price_item.get(
                "currency_code", territory.currency_code
            )

            existing_price = await session.execute(
                select(IAPPrice).where(
                    IAPPrice.iap_id == iap.id,
                    IAPPrice.territory_id == territory.id,
                )
            )
            price_record = existing_price.scalar_one_or_none()
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


@mcp.tool(name="pricing.sync_iap_prices")
async def sync_iap_prices(app_id: int, iap_id: int) -> SyncPricesResponse:
    """Explicitly sync IAP prices from ASC into DB cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)
        territory_map = await _get_territory_map(session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                prices_data = await pricing_service.get_iap_price_schedule(
                    iap.asc_iap_id
                )
            except ASCAPIError as exc:
                raise _asc_error(exc)

        now = datetime.now(timezone.utc)
        prices_synced = 0
        existing_result = await session.execute(
            select(IAPPrice).where(IAPPrice.iap_id == iap.id)
        )
        existing_by_territory = {
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
        return SyncPricesResponse(
            prices_synced=prices_synced, price_points_synced=0,
        )


@mcp.tool(name="pricing.iap_price_points_status")
async def iap_price_points_status(
    app_id: int, iap_id: int,
) -> PricePointCacheStatus:
    """Return the status of the IAP price-point filesystem cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _get_verified_iap(iap_id, app.id, session)
        cache = PricePointCache(product_type="iap")
        info = await cache.status()
        return PricePointCacheStatus(**info)


@mcp.tool(name="pricing.sync_iap_price_points")
async def sync_iap_price_points(
    app_id: int, iap_id: int,
) -> PricePointSyncResponse:
    """Sync Apple IAP price tiers into the app-wide filesystem cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)
        territory_map = await _get_territory_map(session)
        territory_by_id = {t.id: t for t in territory_map.values()}
        territory_codes = sorted({t.code for t in territory_by_id.values()})

        cache = PricePointCache(product_type="iap")
        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            total_points = await cache.fetch_and_cache_all(
                territory_codes, iap.asc_iap_id, pricing_service,
                concurrency=1,
            )
        return PricePointSyncResponse(
            territories_synced=len(territory_codes),
            price_points_total=total_points,
        )


@mcp.tool(name="pricing.preview_iap_prices")
async def preview_iap_prices(
    app_id: int, iap_id: int, request: dict[str, Any],
) -> IAPPricePreviewResponse:
    """Preview suggested IAP prices. ``request`` matches PricePreviewRequest."""
    body = PricePreviewRequest.model_validate(request)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)
        territory_map = await _get_territory_map(session)

        current_prices_result = await session.execute(
            select(IAPPrice).where(IAPPrice.iap_id == iap.id)
        )
        current_price_by_territory = {
            p.territory_id: p for p in current_prices_result.scalars().all()
        }

        cache = PricePointCache(product_type="iap")
        price_points_by_territory: dict[str, list[dict]] = {}
        all_territories = _unique_territories(territory_map)
        for territory in all_territories:
            cached = await cache.get_with_price_point_ids(
                territory.code, iap.asc_iap_id,
            )
            if cached is not None:
                price_points_by_territory[territory.code] = cached

        preview_items = await _preview_items(
            body=body,
            session=session,
            territory_map=territory_map,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
        )

        return IAPPricePreviewResponse(
            iap_id=iap.id,
            iap_name=iap.name,
            index_type=body.index_type,
            base_price=body.base_price,
            items=preview_items,
        )


@mcp.tool(name="pricing.resolve_iap_price")
async def resolve_iap_price(
    app_id: int, iap_id: int, territory_code: str, price: float,
) -> PriceResolveResponse:
    """Resolve a manual IAP price to the nearest Apple price tier (cached)."""
    body = PriceResolveRequest(territory_code=territory_code, price=price)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)

        cache = PricePointCache(product_type="iap")
        pps = await cache.get_with_price_point_ids(
            body.territory_code, iap.asc_iap_id,
        )
        if not pps:
            raise ToolError(
                f"No cached price tiers for territory '{body.territory_code}'. "
                f"Sync price tiers first."
            )
        nearest = min(pps, key=lambda pp: abs(pp["customer_price"] - body.price))
        return PriceResolveResponse(
            territory_code=body.territory_code,
            currency_code=nearest["currency_code"],
            customer_price=nearest["customer_price"],
            proceeds=nearest["proceeds"],
            price_point_id=nearest["price_point_id"],
        )


@mcp.tool(name="pricing.apply_iap_prices")
async def apply_iap_prices(
    app_id: int, iap_id: int, request: dict[str, Any],
) -> PriceApplyResponse:
    """Apply IAP prices via ASC (single-batch schedule replace, ±50% safety)."""
    body = PriceApplyRequest.model_validate(request)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)

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
                        failed += 1
                        errors.append(
                            f"Territory {tc}: failed to fetch price tiers"
                        )
                        continue

            new_price: float | None = None
            for pp in territory_pps:
                if pp["price_point_id"] == item.price_point_id:
                    new_price = pp["customer_price"]
                    break

            if new_price is None:
                failed += 1
                errors.append(
                    f"Territory {tc}: price_point_id "
                    f"{item.price_point_id!r} not found in cached tiers"
                )
                continue

            skip = _safety_skip_item(
                tc,
                current_price=current_price,
                new_price=new_price,
                force=item.force,
            )
            if skip is not None:
                skipped += 1
                skipped_items.append(skip)
                continue

            price_entries.append({
                "territory_code": tc,
                "price_point_id": item.price_point_id,
            })

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

        return PriceApplyResponse(
            applied=applied,
            failed=failed,
            skipped=skipped,
            errors=errors,
            skipped_items=skipped_items,
        )


@mcp.tool(name="pricing.get_iap_review_screenshot")
async def get_iap_review_screenshot(
    app_id: int, iap_id: int,
) -> ReviewScreenshotResponse | None:
    """Get the review screenshot for an IAP, or None."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            data = await pricing_service.get_iap_review_screenshot(iap.asc_iap_id)
        return _parse_screenshot(data)


@mcp.tool(name="pricing.upload_iap_review_screenshot")
async def upload_iap_review_screenshot(
    app_id: int,
    iap_id: int,
    file_name: str,
    file_base64: str,
) -> ReviewScreenshotResponse:
    """Upload a review screenshot for an IAP. ``file_base64`` is the binary."""
    try:
        file_bytes = base64.b64decode(file_base64)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"file_base64 is not valid base64: {exc}")
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        iap = await _get_verified_iap(iap_id, app.id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            pricing_service = ASCPricingService(client)
            try:
                result = await pricing_service.upload_iap_review_screenshot(
                    iap.asc_iap_id,
                    file_name or "screenshot.png",
                    file_bytes,
                )
            except ASCAPIError as exc:
                raise ToolError(f"Screenshot upload failed: {exc.message}")
        parsed = _parse_screenshot(result.get("data"))
        if parsed is None:
            raise ToolError("Screenshot upload succeeded but ASC returned no data")
        return parsed


# ==================================================================
# Price export / import (CSV / XLSX)
# ==================================================================


@mcp.tool(name="pricing.export_prices")
async def export_prices_tool(
    subscription_name: str,
    prices: list[dict[str, Any]],
    format: str = "xlsx",
) -> dict[str, str]:
    """Export prices to a downloadable Excel or CSV file.

    Returns ``{filename, content_type, content_base64}``. Decode the base64
    payload to get the raw file bytes.
    """
    if format not in ("xlsx", "csv"):
        raise ToolError("format must be 'xlsx' or 'csv'")
    filename_base = subscription_name.replace(" ", "_")
    if format == "csv":
        file_bytes = CSVExportService.export_prices(subscription_name, prices)
        return {
            "filename": f"{filename_base}.csv",
            "content_type": "text/csv",
            "content_base64": base64.b64encode(file_bytes).decode("ascii"),
        }
    file_bytes = ExcelExportService.export_prices(subscription_name, prices)
    return {
        "filename": f"{filename_base}.xlsx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "content_base64": base64.b64encode(file_bytes).decode("ascii"),
    }


@mcp.tool(name="pricing.import_prices")
async def import_prices_tool(
    filename: str,
    file_base64: str,
) -> dict[str, Any]:
    """Parse an uploaded Excel or CSV price file (decided by extension).

    Returns ``{count, items}``; items is a list of
    ``{territory_code, customer_price}``.
    """
    try:
        file_bytes = base64.b64decode(file_base64)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"file_base64 is not valid base64: {exc}")
    if not file_bytes:
        raise ToolError("Uploaded file is empty")

    fname_lower = (filename or "").lower()
    if fname_lower.endswith(".csv"):
        parsed = CSVExportService.import_prices(file_bytes)
    elif fname_lower.endswith((".xlsx", ".xls")):
        parsed = ExcelExportService.import_prices(file_bytes)
    else:
        raise ToolError("Unsupported file format. Use .xlsx or .csv files.")

    items = [
        {
            "territory_code": p["territory_code"],
            "customer_price": p["customer_price"],
        }
        for p in parsed
    ]
    return {"count": len(items), "items": items}
