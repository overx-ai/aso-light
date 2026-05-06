"""RevenueCat integration routes — credential management + product/
entitlement/offering/package CRUD proxied through the RC v2 API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import decrypt_value, encrypt_value, get_current_user
from app.db.session import get_session
from app.models.app import App
from app.models.revenuecat_credential import RevenueCatCredential
from app.schemas.revenuecat import (
    RCAttachProductsRequest,
    RCConnectionTestResponse,
    RCEntitlementCreate,
    RCEntitlementUpdate,
    RCOfferingCreate,
    RCOfferingUpdate,
    RCPackageCreate,
    RevenueCatCredentialCreate,
    RevenueCatCredentialResponse,
    RevenueCatCredentialUpdate,
)
from app.services.revenuecat.client import RevenueCatClient
from app.services.revenuecat.errors import RevenueCatAPIError

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cred_to_response(c: RevenueCatCredential) -> RevenueCatCredentialResponse:
    return RevenueCatCredentialResponse(
        id=c.id,
        name=c.name,
        project_id=c.project_id,
        rc_app_id=c.rc_app_id,
        created_at=c.created_at,
    )


async def _resolve_rc_client_for_app(
    app: App, user_id: int, session: AsyncSession,
) -> tuple[RevenueCatClient, RevenueCatCredential]:
    if app.revenuecat_credential_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No RevenueCat credential linked to this app. "
                "POST /apps/{id}/revenuecat/credential first."
            ),
        )
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Linked RevenueCat credential not found for this user",
        )
    client = RevenueCatClient.from_credential(cred)
    return client, cred


def _wrap_rc_error(exc: RevenueCatAPIError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"RevenueCat error: {exc.message}",
    )


# ---------------------------------------------------------------------------
# Credential CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/{app_id}/revenuecat/credential",
    response_model=RevenueCatCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_or_update_credential(
    app_id: int,
    body: RevenueCatCredentialCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RevenueCatCredentialResponse:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    if app.revenuecat_credential_id is not None:
        res = await session.execute(
            select(RevenueCatCredential).where(
                RevenueCatCredential.id == app.revenuecat_credential_id,
                RevenueCatCredential.user_id == user_id,
            )
        )
        cred = res.scalar_one_or_none()
    else:
        cred = None

    if cred is None:
        cred = RevenueCatCredential(
            user_id=user_id,
            name=body.name,
            project_id=body.project_id,
            rc_app_id=body.rc_app_id,
            secret_key_encrypted=encrypt_value(body.secret_key),
        )
        session.add(cred)
        await session.flush()
        app.revenuecat_credential_id = cred.id
    else:
        cred.name = body.name
        cred.project_id = body.project_id
        cred.rc_app_id = body.rc_app_id
        cred.secret_key_encrypted = encrypt_value(body.secret_key)

    await session.flush()
    return _cred_to_response(cred)


@router.get(
    "/{app_id}/revenuecat/credential",
    response_model=RevenueCatCredentialResponse | None,
)
async def get_credential(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RevenueCatCredentialResponse | None:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if app.revenuecat_credential_id is None:
        return None
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    return _cred_to_response(cred) if cred else None


@router.patch(
    "/{app_id}/revenuecat/credential",
    response_model=RevenueCatCredentialResponse,
)
async def update_credential(
    app_id: int,
    body: RevenueCatCredentialUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RevenueCatCredentialResponse:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if app.revenuecat_credential_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No credential to update",
        )
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    if body.name is not None:
        cred.name = body.name
    if body.project_id is not None:
        cred.project_id = body.project_id
    if body.rc_app_id is not None:
        cred.rc_app_id = body.rc_app_id
    if body.secret_key:
        cred.secret_key_encrypted = encrypt_value(body.secret_key)
    await session.flush()
    return _cred_to_response(cred)


@router.delete(
    "/{app_id}/revenuecat/credential",
    status_code=status.HTTP_200_OK,
)
async def delete_credential(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if app.revenuecat_credential_id is None:
        return {"detail": "No credential linked"}
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    app.revenuecat_credential_id = None
    if cred is not None:
        await session.delete(cred)
    return {"detail": "RevenueCat credential disconnected"}


@router.post(
    "/{app_id}/revenuecat/credential/test",
    response_model=RCConnectionTestResponse,
)
async def test_credential(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RCConnectionTestResponse:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    try:
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    except HTTPException as exc:
        return RCConnectionTestResponse(
            success=False, message=exc.detail,
        )
    try:
        async with client:
            apps = await client.list_apps()
        return RCConnectionTestResponse(
            success=True,
            message=f"Connected. Project has {len(apps)} app(s).",
            apps_count=len(apps),
        )
    except RevenueCatAPIError as exc:
        return RCConnectionTestResponse(
            success=False, message=f"RevenueCat error: {exc.message}",
        )


# ---------------------------------------------------------------------------
# RC apps (read-only)
# ---------------------------------------------------------------------------


@router.get("/{app_id}/revenuecat/apps", response_model=list[dict])
async def list_rc_apps(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.list_apps()
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


# ---------------------------------------------------------------------------
# Products (read-only — products are managed by the clone flow)
# ---------------------------------------------------------------------------


@router.get("/{app_id}/revenuecat/products", response_model=list[dict])
async def list_products(
    app_id: int,
    store_identifier: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, cred = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.list_products(
                app_id=cred.rc_app_id,
                store_identifier=store_identifier,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/products/{rc_product_id}/archive",
    response_model=dict,
)
async def archive_product(
    app_id: int,
    rc_product_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.archive_product(rc_product_id)
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


# ---------------------------------------------------------------------------
# Entitlements
# ---------------------------------------------------------------------------


@router.get("/{app_id}/revenuecat/entitlements", response_model=list[dict])
async def list_entitlements(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.list_entitlements()
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/entitlements",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_entitlement(
    app_id: int,
    body: RCEntitlementCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.create_entitlement(
                lookup_key=body.lookup_key,
                display_name=body.display_name,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.patch(
    "/{app_id}/revenuecat/entitlements/{entitlement_id}",
    response_model=dict,
)
async def update_entitlement(
    app_id: int,
    entitlement_id: str,
    body: RCEntitlementUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.update_entitlement(
                entitlement_id=entitlement_id,
                display_name=body.display_name,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.delete(
    "/{app_id}/revenuecat/entitlements/{entitlement_id}",
    response_model=dict,
)
async def archive_entitlement(
    app_id: int,
    entitlement_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.archive_entitlement(entitlement_id)
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/entitlements/{entitlement_id}/attach",
    response_model=dict,
)
async def attach_products_to_entitlement(
    app_id: int,
    entitlement_id: str,
    body: RCAttachProductsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.attach_products_to_entitlement(
                entitlement_id=entitlement_id,
                product_ids=body.product_ids,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/entitlements/{entitlement_id}/detach",
    response_model=dict,
)
async def detach_products_from_entitlement(
    app_id: int,
    entitlement_id: str,
    body: RCAttachProductsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.detach_products_from_entitlement(
                entitlement_id=entitlement_id,
                product_ids=body.product_ids,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


# ---------------------------------------------------------------------------
# Offerings
# ---------------------------------------------------------------------------


@router.get("/{app_id}/revenuecat/offerings", response_model=list[dict])
async def list_offerings(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.list_offerings()
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/offerings",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_offering(
    app_id: int,
    body: RCOfferingCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.create_offering(
                lookup_key=body.lookup_key,
                display_name=body.display_name,
                is_current=body.is_current,
                metadata=body.metadata,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.patch(
    "/{app_id}/revenuecat/offerings/{offering_id}",
    response_model=dict,
)
async def update_offering(
    app_id: int,
    offering_id: str,
    body: RCOfferingUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.update_offering(
                offering_id=offering_id,
                display_name=body.display_name,
                is_current=body.is_current,
                metadata=body.metadata,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.delete(
    "/{app_id}/revenuecat/offerings/{offering_id}",
    response_model=dict,
)
async def archive_offering(
    app_id: int,
    offering_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.archive_offering(offering_id)
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


@router.get(
    "/{app_id}/revenuecat/offerings/{offering_id}/packages",
    response_model=list[dict],
)
async def list_packages(
    app_id: int,
    offering_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.list_packages(offering_id)
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/offerings/{offering_id}/packages",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_package(
    app_id: int,
    offering_id: str,
    body: RCPackageCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.create_package(
                offering_id=offering_id,
                lookup_key=body.lookup_key,
                display_name=body.display_name,
                position=body.position,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.delete(
    "/{app_id}/revenuecat/offerings/{offering_id}/packages/{package_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_package(
    app_id: int,
    offering_id: str,
    package_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            await client.delete_package(offering_id, package_id)
        return {"detail": "Package deleted"}
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/offerings/{offering_id}/packages/"
    "{package_id}/attach",
    response_model=dict,
)
async def attach_products_to_package(
    app_id: int,
    offering_id: str,
    package_id: str,
    body: RCAttachProductsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.attach_products_to_package(
                offering_id=offering_id,
                package_id=package_id,
                product_ids=body.product_ids,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc


@router.post(
    "/{app_id}/revenuecat/offerings/{offering_id}/packages/"
    "{package_id}/detach",
    response_model=dict,
)
async def detach_products_from_package(
    app_id: int,
    offering_id: str,
    package_id: str,
    body: RCAttachProductsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client, _ = await _resolve_rc_client_for_app(app, user_id, session)
    try:
        async with client:
            return await client.detach_products_from_package(
                offering_id=offering_id,
                package_id=package_id,
                product_ids=body.product_ids,
            )
    except RevenueCatAPIError as exc:
        raise _wrap_rc_error(exc) from exc
