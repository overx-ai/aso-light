"""Clone-and-version-bump routes for subscriptions and IAPs.

Mounted under ``/apps``. The cloner runs synchronously in-request — at
most a few minutes for the largest sub (locales × territories × intro
offers). The CloneOperation row is the source of truth for status, so
the frontend can poll ``GET /clone-operations/{id}`` after a partial
failure and retry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.app import App
from app.models.clone_operation import CloneOperation
from app.models.iap import InAppPurchase
from app.models.revenuecat_credential import RevenueCatCredential
from app.models.subscription import Subscription, SubscriptionGroup
from app.schemas.clone import (
    CloneOperationOut,
    ClonePreviewResponse,
    CloneRequest,
    CloneScope,
    CloneStepStatus,
)
from app.services.asc.clone import (
    IAPCloner,
    SubscriptionCloner,
    next_versioned_product_id,
)
from app.services.asc.pricing import ASCPricingService
from app.services.revenuecat.client import RevenueCatClient
from app.services.revenuecat.errors import RevenueCatAPIError
from app.services.revenuecat.swap import RevenueCatProductSwap

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Period -> ISO 8601 helper for RC subscription duration
# ---------------------------------------------------------------------------


_PERIOD_TO_ISO: dict[str, str] = {
    "ONE_WEEK": "P1W",
    "ONE_MONTH": "P1M",
    "TWO_MONTHS": "P2M",
    "THREE_MONTHS": "P3M",
    "SIX_MONTHS": "P6M",
    "ONE_YEAR": "P1Y",
}

# ASC inAppPurchaseType -> RevenueCat product_type. Shared by the REST
# clone_iap route and the MCP swap.iap tool so the mapping lives once.
_IAP_TYPE_TO_RC: dict[str, str] = {
    "CONSUMABLE": "consumable",
    "NON_CONSUMABLE": "non_consumable",
    "NON_RENEWING_SUBSCRIPTION": "non_renewable",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_verified_subscription(
    subscription_id: int, app_id: int, session: AsyncSession,
) -> Subscription:
    res = await session.execute(
        select(Subscription)
        .join(SubscriptionGroup)
        .where(
            Subscription.id == subscription_id,
            SubscriptionGroup.app_id == app_id,
        )
    )
    sub = res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found for this app",
        )
    return sub


async def _get_verified_iap(
    iap_id: int, app_id: int, session: AsyncSession,
) -> InAppPurchase:
    res = await session.execute(
        select(InAppPurchase).where(
            InAppPurchase.id == iap_id,
            InAppPurchase.app_id == app_id,
        )
    )
    iap = res.scalar_one_or_none()
    if iap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="IAP not found for this app",
        )
    return iap


async def _get_rc_credential(
    app: App, user_id: int, session: AsyncSession,
) -> RevenueCatCredential | None:
    if app.revenuecat_credential_id is None:
        return None
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    return res.scalar_one_or_none()


def _row_to_out(row: CloneOperation) -> CloneOperationOut:
    return CloneOperationOut(
        id=row.id,
        app_id=row.app_id,
        source_kind=row.source_kind,
        source_local_id=row.source_local_id,
        source_product_id=row.source_product_id,
        target_product_id=row.target_product_id,
        source_asc_id=row.source_asc_id,
        target_asc_id=row.target_asc_id,
        scope=CloneScope(**(row.scope_json or {})),
        asc_steps=[CloneStepStatus(**s) for s in (row.asc_steps_json or [])],
        revenuecat_steps=[
            CloneStepStatus(**s) for s in (row.revenuecat_steps_json or [])
        ],
        status=row.status,
        error_log=row.error_log_json or [],
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _normalize_steps(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [{"name": k, **v} for k, v in raw.items()]
    return []


def _overall_status(asc_errs: list[str], rc_errs: list[str]) -> str:
    """``partial`` if either side reported errors, else ``done``."""
    if asc_errs or rc_errs:
        return "partial"
    return "done"


ARCHIVE_INCOMPLETE_WARNING = (
    "ASC archive incomplete — verify the old product is off sale before "
    "relying on the swap."
)
IAP_STILL_LIVE_WARNING = (
    "Old IAP is still live in the App Store — remove it from your next App "
    "Version submission manually; keep honoring it server-side until then."
)


def _swap_warnings(source_kind: str, archive_status: str | None) -> list[str]:
    """Operator-facing warnings derived from the ASC archive outcome.

    Distinguishes a true archive failure (subscription) and the
    Apple-can't-archive-an-IAP case from a cosmetic partial, so the
    operator is never told "all good" while the old product is still on
    sale (A-I2 / A-I3).
    """
    warnings: list[str] = []
    if source_kind == "iap":
        # Apple has no IAP-archive API: the old IAP always stays live.
        if archive_status in {"skipped", "failed", None}:
            warnings.append(IAP_STILL_LIVE_WARNING)
    elif archive_status in {"failed", "skipped"}:
        warnings.append(ARCHIVE_INCOMPLETE_WARNING)
    return warnings


async def finalize_swap(
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
) -> dict[str, Any]:
    """Run the optional RC swap and finalize the CloneOperation row.

    Single source of truth for the "set target_asc_id is already done →
    run RC swap → compute status → set completed_at → derive swap-health
    flags + warnings" sequence shared by REST ``clone_subscription`` /
    ``clone_iap`` and the MCP ``swap.*`` finalize.

    ``op.target_asc_id``/``op.asc_steps_json`` must already be populated
    from the cloner result. Mutates ``op`` (status, error log, RC steps,
    completed_at). Returns the health flags + warnings so each caller can
    shape its own response (REST ``CloneOperationOut`` vs MCP
    ``SwapResponse``).
    """
    rc_errs: list[str] = []
    rc_cred = await _get_rc_credential(app, user_id, session)
    rc_connected = rc_cred is not None

    if swap_revenuecat and op.target_asc_id and rc_cred is not None:
        async with RevenueCatClient.from_credential(rc_cred) as rc_client:
            swap = RevenueCatProductSwap(rc_client, rc_cred.rc_app_id)
            try:
                swap_result = await swap.swap(
                    old_store_id=old_product_id,
                    new_store_id=new_product_id,
                    product_type=product_type,
                    subscription_period=subscription_period,
                    display_name=display_name,
                )
                op.revenuecat_steps_json = _normalize_steps(
                    swap_result.get("steps"),
                )
                rc_errs = swap_result.get("errors") or []
            except RevenueCatAPIError as exc:
                rc_errs = [f"swap: {exc}"]
                op.revenuecat_steps_json = [
                    {"name": "swap", "status": "failed", "detail": str(exc)},
                ]

    warnings = _swap_warnings(source_kind, archive_status)

    if op.target_asc_id is None:
        op.status = "failed"
    else:
        op.status = _overall_status(asc_errs, rc_errs)
    op.error_log_json = (
        asc_errs + [f"revenuecat: {e}" for e in rc_errs] + warnings
    )
    if op.status in {"done", "partial"}:
        op.completed_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(op)

    # A-I1: ``rc_swap_ok`` must reflect ASC health, not just RC. If the
    # ASC archive failed, the OLD product is still on sale and the iOS
    # "no change required" path must NOT be claimed.
    rc_swap_ok = (
        rc_connected
        and not rc_errs
        and not asc_errs
        and op.target_asc_id is not None
    )
    return {
        "rc_connected": rc_connected,
        "rc_swap_ok": rc_swap_ok,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Preview routes
# ---------------------------------------------------------------------------


@router.get(
    "/{app_id}/subscriptions/{sub_id}/clone/preview",
    response_model=ClonePreviewResponse,
)
async def preview_clone_subscription(
    app_id: int,
    sub_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ClonePreviewResponse:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    sub = await _get_verified_subscription(sub_id, app.id, session)
    suggested = next_versioned_product_id(sub.product_id)

    # Counts via DB (cheap)
    from app.models.subscription import SubscriptionPrice

    res = await session.execute(
        select(SubscriptionPrice).where(
            SubscriptionPrice.subscription_id == sub.id,
        )
    )
    prices_count = len(res.scalars().all())

    # Locales + intro offers + screenshot via ASC (small, focused calls)
    client = await _get_asc_client_for_app(app, session)
    pricing = ASCPricingService(client)
    try:
        locales = await pricing.list_subscription_localizations(
            sub.asc_subscription_id,
        )
        offers = await pricing.list_subscription_introductory_offers(
            sub.asc_subscription_id,
        )
        shot = await pricing.get_subscription_review_screenshot(
            sub.asc_subscription_id,
        )
    finally:
        await client.close()

    # RC linkage
    rc_cred = await _get_rc_credential(app, user_id, session)
    rc_old_found = False
    rc_ent_count = 0
    rc_pkg_count = 0
    if rc_cred:
        async with RevenueCatClient.from_credential(rc_cred) as rc_client:
            try:
                products = await rc_client.list_products(
                    app_id=rc_cred.rc_app_id,
                    store_identifier=sub.product_id,
                )
                rc_old_found = any(
                    p.get("store_identifier") == sub.product_id
                    for p in products
                )
                if rc_old_found:
                    target = next(
                        p for p in products
                        if p.get("store_identifier") == sub.product_id
                    )
                    target_id = target["id"]
                    entitlements = await rc_client.list_entitlements()
                    for ent in entitlements:
                        for prod in (ent.get("products") or []):
                            if (
                                prod.get("id") == target_id
                                or prod.get("store_identifier")
                                == sub.product_id
                            ):
                                rc_ent_count += 1
                                break
                    offerings = await rc_client.list_offerings()
                    for off in offerings:
                        try:
                            packages = await rc_client.list_packages(
                                off["id"],
                            )
                        except RevenueCatAPIError:
                            continue
                        for pkg in packages:
                            for prod in (pkg.get("products") or []):
                                if (
                                    prod.get("id") == target_id
                                    or prod.get("store_identifier")
                                    == sub.product_id
                                ):
                                    rc_pkg_count += 1
                                    break
            except RevenueCatAPIError as exc:
                logger.warning("RC preview failed: %s", exc)

    return ClonePreviewResponse(
        suggested_product_id=suggested,
        source_product_id=sub.product_id,
        locale_count=len(locales),
        priced_territory_count=prices_count,
        intro_offer_count=len(offers),
        has_screenshot=shot is not None,
        revenuecat_connected=rc_cred is not None,
        revenuecat_old_product_found=rc_old_found,
        revenuecat_attached_entitlements=rc_ent_count,
        revenuecat_attached_packages=rc_pkg_count,
    )


@router.get(
    "/{app_id}/iaps/{iap_id}/clone/preview",
    response_model=ClonePreviewResponse,
)
async def preview_clone_iap(
    app_id: int,
    iap_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ClonePreviewResponse:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)
    suggested = next_versioned_product_id(iap.product_id)

    from app.models.iap import IAPPrice

    res = await session.execute(
        select(IAPPrice).where(IAPPrice.iap_id == iap.id),
    )
    prices_count = len(res.scalars().all())

    client = await _get_asc_client_for_app(app, session)
    pricing = ASCPricingService(client)
    try:
        locales = await pricing.list_iap_localizations(iap.asc_iap_id)
        shot = await pricing.get_iap_review_screenshot(iap.asc_iap_id)
    finally:
        await client.close()

    rc_cred = await _get_rc_credential(app, user_id, session)
    rc_old_found = False
    if rc_cred:
        async with RevenueCatClient.from_credential(rc_cred) as rc_client:
            try:
                products = await rc_client.list_products(
                    app_id=rc_cred.rc_app_id,
                    store_identifier=iap.product_id,
                )
                rc_old_found = any(
                    p.get("store_identifier") == iap.product_id
                    for p in products
                )
            except RevenueCatAPIError:
                pass

    return ClonePreviewResponse(
        suggested_product_id=suggested,
        source_product_id=iap.product_id,
        locale_count=len(locales),
        priced_territory_count=prices_count,
        intro_offer_count=0,
        has_screenshot=shot is not None,
        revenuecat_connected=rc_cred is not None,
        revenuecat_old_product_found=rc_old_found,
        revenuecat_attached_entitlements=0,
        revenuecat_attached_packages=0,
    )


# ---------------------------------------------------------------------------
# Clone routes
# ---------------------------------------------------------------------------


@router.post(
    "/{app_id}/subscriptions/{sub_id}/clone",
    response_model=CloneOperationOut,
)
async def clone_subscription(
    app_id: int,
    sub_id: int,
    body: CloneRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CloneOperationOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    sub = await _get_verified_subscription(sub_id, app.id, session)

    op = CloneOperation(
        app_id=app.id,
        user_id=user_id,
        source_kind="subscription",
        source_local_id=sub.id,
        source_asc_id=sub.asc_subscription_id,
        source_product_id=sub.product_id,
        target_product_id=body.new_product_id,
        scope_json=body.scope.model_dump(),
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
            new_product_id=body.new_product_id,
            new_name=body.new_name,
            scope=body.scope.model_dump(),
        )
    finally:
        await client.close()

    op.target_asc_id = result.get("target_asc_id")
    op.asc_steps_json = result.get("steps") or []
    asc_errs = list(result.get("errors") or [])

    await finalize_swap(
        op=op,
        app=app,
        user_id=user_id,
        session=session,
        swap_revenuecat=body.swap_revenuecat,
        old_product_id=sub.product_id,
        new_product_id=body.new_product_id,
        product_type="subscription",
        display_name=body.new_name or sub.name,
        asc_errs=asc_errs,
        source_kind="subscription",
        archive_status=result.get("archive_status"),
        subscription_period=_PERIOD_TO_ISO.get(
            result.get("subscription_period", ""),
        ),
    )
    return _row_to_out(op)


@router.post(
    "/{app_id}/iaps/{iap_id}/clone",
    response_model=CloneOperationOut,
)
async def clone_iap(
    app_id: int,
    iap_id: int,
    body: CloneRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CloneOperationOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    iap = await _get_verified_iap(iap_id, app.id, session)

    op = CloneOperation(
        app_id=app.id,
        user_id=user_id,
        source_kind="iap",
        source_local_id=iap.id,
        source_asc_id=iap.asc_iap_id,
        source_product_id=iap.product_id,
        target_product_id=body.new_product_id,
        scope_json=body.scope.model_dump(),
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
            new_product_id=body.new_product_id,
            new_name=body.new_name,
            scope=body.scope.model_dump(),
        )
    finally:
        await client.close()

    op.target_asc_id = result.get("target_asc_id")
    op.asc_steps_json = result.get("steps") or []
    asc_errs = list(result.get("errors") or [])

    await finalize_swap(
        op=op,
        app=app,
        user_id=user_id,
        session=session,
        swap_revenuecat=body.swap_revenuecat,
        old_product_id=iap.product_id,
        new_product_id=body.new_product_id,
        product_type=_IAP_TYPE_TO_RC.get(iap.iap_type, "non_consumable"),
        display_name=body.new_name or iap.name,
        asc_errs=asc_errs,
        source_kind="iap",
        archive_status=result.get("archive_status"),
    )
    return _row_to_out(op)


@router.get(
    "/{app_id}/clone-operations/{op_id}",
    response_model=CloneOperationOut,
)
async def get_clone_operation(
    app_id: int,
    op_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CloneOperationOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    res = await session.execute(
        select(CloneOperation).where(
            CloneOperation.id == op_id,
            CloneOperation.app_id == app_id,
            CloneOperation.user_id == user_id,
        )
    )
    op = res.scalar_one_or_none()
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clone operation not found",
        )
    return _row_to_out(op)


@router.get(
    "/{app_id}/clone-operations",
    response_model=list[CloneOperationOut],
)
async def list_clone_operations(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CloneOperationOut]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    res = await session.execute(
        select(CloneOperation)
        .where(
            CloneOperation.app_id == app_id,
            CloneOperation.user_id == user_id,
        )
        .order_by(CloneOperation.created_at.desc())
        .limit(100)
    )
    rows = res.scalars().all()
    return [_row_to_out(r) for r in rows]


@router.post(
    "/{app_id}/clone-operations/{op_id}/retry",
    response_model=CloneOperationOut,
)
async def retry_clone_operation(
    app_id: int,
    op_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CloneOperationOut:
    """Re-run the clone for the same target productId.

    Idempotent: cloners detect already-created ASC objects and skip
    successful steps, so retry is safe even when the previous run
    partially succeeded.
    """
    user_id = int(current_user["user_id"])
    # Ownership gate — raises 404 if the user does not own the app.
    await _get_verified_app(app_id, user_id, session)

    res = await session.execute(
        select(CloneOperation).where(
            CloneOperation.id == op_id,
            CloneOperation.app_id == app_id,
            CloneOperation.user_id == user_id,
        )
    )
    op = res.scalar_one_or_none()
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clone operation not found",
        )

    body = CloneRequest(
        new_product_id=op.target_product_id,
        scope=CloneScope(**(op.scope_json or {})),
        swap_revenuecat=True,
    )

    if op.source_kind == "subscription":
        return await clone_subscription(
            app_id=app_id,
            sub_id=op.source_local_id,
            body=body,
            current_user=current_user,
            session=session,
        )
    return await clone_iap(
        app_id=app_id,
        iap_id=op.source_local_id,
        body=body,
        current_user=current_user,
        session=session,
    )
