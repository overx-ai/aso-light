"""MCP swap tools — productId swap for subscriptions and IAPs.

Wraps the existing clone subsystem (clone + auto_archive + RC swap) and
returns a :class:`SwapResponse` whose ``ios_checklist`` field tells the
operator exactly what their iOS app must change. The doc lives at
``docs/006-product-swap-ios-integration.md``; this module produces a
tailored subset based on the actual swap outcome.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app
from app.api.v1.clone import (
    _IAP_TYPE_TO_RC,
    _PERIOD_TO_ISO,
    _row_to_out,
    finalize_swap,
)
from app.mcp.context import (
    get_user_id,
    resolve_app,
    resolve_iap,
    resolve_subscription,
    session_scope,
)
from app.mcp.server import mcp
from app.models.app import App
from app.models.clone_operation import CloneOperation
from app.schemas.clone import CloneScope
from app.schemas.swap import SwapResponse
from app.services.asc.clone import (
    IAPCloner,
    SubscriptionCloner,
    next_versioned_product_id,
)
from app.services.asc.pricing import ASCPricingService

IOS_DOC_PATH = "docs/006-product-swap-ios-integration.md"
TRANSITION_NOTE = (
    "The old productId is archived, not revoked. Existing subscribers keep "
    "billing on it until they cancel or their renewal lapses — typically "
    "weeks or months. Your entitlement-check / receipt-validation must "
    "accept BOTH old and new productIds during this transition window."
)


def _ios_checklist(
    rc_connected: bool,
    rc_swap_ok: bool,
    target_asc_id: str | None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Compose the iOS-side checklist from the swap outcome.

    ``warnings`` (ASC archive incomplete / old IAP still live) are
    surfaced first and prominently, so the operator is never told "no
    iOS change required" while the OLD product is still on sale.
    """
    warnings = warnings or []
    if target_asc_id is None:
        return [
            *warnings,
            "Swap did not produce a new ASC product — do not change iOS yet.",
            "Re-run the swap or inspect the failed steps before touching the app.",
        ]

    items: list[str] = list(warnings)
    if rc_connected and rc_swap_ok:
        items += [
            "PATH 1 (RC + offerings): no iOS code change required if your app "
            "uses Purchases.shared.getOfferings() and reads packages from "
            "currentOffering.availablePackages. Confirm the next offerings "
            "fetch returns the new productId on the same package id, then "
            "sandbox-test a purchase end-to-end.",
            "PATH 2 (RC + hardcoded productIds): update every "
            "Purchases.shared.getProducts([\"...\"]) call site to the new "
            "productId and ship a new app version. Migrate Path 2 callers to "
            "Path 1 while you're here.",
            "Backend / receipt validation: add the new productId to the "
            "entitlement lookup set additively. Do NOT remove the old "
            "productId — existing subscribers still bill on it.",
        ]
    elif rc_connected and not rc_swap_ok:
        items += [
            "RevenueCat swap reported errors — fix RC linkage manually before "
            "any iOS work. Re-attach the new productId to the entitlements + "
            "offering packages that previously held the old productId.",
            f"Once RC is reconciled, follow Path 1 / Path 2 guidance in {IOS_DOC_PATH}.",
        ]
    else:
        items += [
            "PATH 3 (direct StoreKit, no RevenueCat): replace the old "
            "productId with the new one in Product.products(for: [...]) (or "
            "SKProductsRequest) and ship a new app version.",
            "Server-side receipt validation must accept BOTH old and new "
            "productIds as granting the same entitlement. Add the new id "
            "additively; keep the old id while the long tail of subscribers "
            "renews or churns.",
        ]

    items += [
        "Re-test free-trial / intro-offer eligibility — Apple grants one intro "
        "per subscription group lifetime, and the group identity is preserved "
        "by the swap.",
        "Verify in sandbox: a fresh tester can buy through the offering / new "
        "productId and the entitlement activates on device.",
        f"Full guidance and sample code: {IOS_DOC_PATH}.",
    ]
    return items


def _to_swap_response(
    op_out_dict: dict,
    rc_connected: bool,
    rc_swap_ok: bool,
    target_asc_id: str | None,
    warnings: list[str],
) -> SwapResponse:
    return SwapResponse(
        **op_out_dict,
        ios_checklist=_ios_checklist(
            rc_connected, rc_swap_ok, target_asc_id, warnings,
        ),
        ios_doc_url=IOS_DOC_PATH,
        transition_window_note=TRANSITION_NOTE,
    )


async def _finalize_swap(
    *,
    op: CloneOperation,
    app: App,
    user_id: int,
    session: AsyncSession,
    swap_revenuecat: bool,
    old_product_id: str,
    new_product_id: str,
    product_type: str,
    display_name: str,
    asc_errs: list[str],
    source_kind: str,
    archive_status: str | None,
    subscription_period: str | None = None,
) -> SwapResponse:
    """Finalize the swap and build the :class:`SwapResponse`.

    Delegates the RC swap + status/timestamp/health computation to the
    shared :func:`app.api.v1.clone.finalize_swap` so the A-I1/A-I2/A-I3
    fixes live in one place; this only shapes the MCP-specific
    ``SwapResponse`` (checklist + doc + transition note) at the edge.
    """
    health = await finalize_swap(
        op=op,
        app=app,
        user_id=user_id,
        session=session,
        swap_revenuecat=swap_revenuecat,
        old_product_id=old_product_id,
        new_product_id=new_product_id,
        product_type=product_type,
        display_name=display_name,
        asc_errs=asc_errs,
        source_kind=source_kind,
        archive_status=archive_status,
        subscription_period=subscription_period,
    )

    return _to_swap_response(
        op_out_dict=_row_to_out(op).model_dump(),
        rc_connected=health["rc_connected"],
        rc_swap_ok=health["rc_swap_ok"],
        target_asc_id=op.target_asc_id,
        warnings=health["warnings"],
    )


@mcp.tool(name="swap.subscription_product")
async def swap_subscription_product(
    app_id: int,
    subscription_id: int,
    new_product_id: str,
    new_name: str | None = None,
    auto_archive: bool = True,
    swap_revenuecat: bool = True,
) -> SwapResponse:
    """Swap a subscription's productId end-to-end (ASC + RevenueCat).

    Creates a new subscription in the same SubscriptionGroup with
    ``new_product_id``, copies localizations + price schedule + intro offers
    + review screenshot, archives the old subscription (if ``auto_archive``),
    then re-points RevenueCat entitlements + offering packages to the new
    productId (if ``swap_revenuecat`` and RC is configured).

    Returns a SwapResponse including an iOS-side checklist tailored to the
    actual outcome.
    """
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        sub = await resolve_subscription(subscription_id, app.id, session)

        scope = CloneScope(auto_archive=auto_archive)
        op = CloneOperation(
            app_id=app.id,
            user_id=user_id,
            source_kind="subscription",
            source_local_id=sub.id,
            source_asc_id=sub.asc_subscription_id,
            source_product_id=sub.product_id,
            target_product_id=new_product_id,
            scope_json=scope.model_dump(),
            asc_steps_json=[],
            revenuecat_steps_json=[],
            status="pending",
            error_log_json=[],
        )
        session.add(op)
        await session.flush()

        client = await _get_asc_client_for_app(app, session)
        pricing = ASCPricingService(client)
        cloner = SubscriptionCloner(
            pricing=pricing,
            session=session,
            app_id=app.id,
            app_asc_id=app.asc_app_id,
        )
        try:
            result = await cloner.clone(
                source=sub,
                new_product_id=new_product_id,
                new_name=new_name,
                scope=scope.model_dump(),
            )
        finally:
            await client.close()

        op.target_asc_id = result.get("target_asc_id")
        op.asc_steps_json = result.get("steps") or []
        asc_errs = list(result.get("errors") or [])

        return await _finalize_swap(
            op=op,
            app=app,
            user_id=user_id,
            session=session,
            swap_revenuecat=swap_revenuecat,
            old_product_id=sub.product_id,
            new_product_id=new_product_id,
            product_type="subscription",
            display_name=new_name or sub.name,
            asc_errs=asc_errs,
            source_kind="subscription",
            archive_status=result.get("archive_status"),
            subscription_period=_PERIOD_TO_ISO.get(
                result.get("subscription_period", ""),
            ),
        )


@mcp.tool(name="swap.iap")
async def swap_iap(
    app_id: int,
    iap_id: int,
    new_product_id: str,
    new_name: str | None = None,
    auto_archive: bool = True,
    swap_revenuecat: bool = True,
) -> SwapResponse:
    """Swap an IAP's productId end-to-end (ASC + RevenueCat).

    Same shape as ``swap.subscription_product`` but for non-renewing /
    consumable / non-consumable IAPs.
    """
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        iap = await resolve_iap(iap_id, app.id, session)

        scope = CloneScope(auto_archive=auto_archive)
        op = CloneOperation(
            app_id=app.id,
            user_id=user_id,
            source_kind="iap",
            source_local_id=iap.id,
            source_asc_id=iap.asc_iap_id,
            source_product_id=iap.product_id,
            target_product_id=new_product_id,
            scope_json=scope.model_dump(),
            asc_steps_json=[],
            revenuecat_steps_json=[],
            status="pending",
            error_log_json=[],
        )
        session.add(op)
        await session.flush()

        client = await _get_asc_client_for_app(app, session)
        pricing = ASCPricingService(client)
        cloner = IAPCloner(
            pricing=pricing,
            session=session,
            app_id=app.id,
            app_asc_id=app.asc_app_id,
        )
        try:
            result = await cloner.clone(
                source=iap,
                new_product_id=new_product_id,
                new_name=new_name,
                scope=scope.model_dump(),
            )
        finally:
            await client.close()

        op.target_asc_id = result.get("target_asc_id")
        op.asc_steps_json = result.get("steps") or []
        asc_errs = list(result.get("errors") or [])

        return await _finalize_swap(
            op=op,
            app=app,
            user_id=user_id,
            session=session,
            swap_revenuecat=swap_revenuecat,
            old_product_id=iap.product_id,
            new_product_id=new_product_id,
            product_type=_IAP_TYPE_TO_RC.get(iap.iap_type, "non_consumable"),
            display_name=new_name or iap.name,
            asc_errs=asc_errs,
            source_kind="iap",
            archive_status=result.get("archive_status"),
        )


@mcp.tool(name="swap.suggest_new_product_id")
async def suggest_new_product_id(current_product_id: str) -> str:
    """Suggest the next versioned productId for a swap.

    Recognizes ``.v{n}`` and ``_v{n}`` suffix styles and bumps in place;
    plain ids become ``{id}.v2``.
    """
    return next_versioned_product_id(current_product_id)
