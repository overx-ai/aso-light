"""MCP tools for the RevenueCat integration.

Mirrors ``app/api/v1/revenuecat.py``: credential CRUD + connection test,
RC apps, products, entitlements, offerings, and packages CRUD proxied
through the RevenueCat v2 API.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value
from app.mcp.context import get_user_id, resolve_app, session_scope
from app.mcp.server import mcp
from app.models.app import App
from app.models.revenuecat_credential import RevenueCatCredential
from app.schemas.revenuecat import (
    RCConnectionTestResponse,
    RevenueCatCredentialResponse,
)
from app.services.revenuecat.client import RevenueCatClient
from app.services.revenuecat.errors import RevenueCatAPIError

logger = logging.getLogger(__name__)


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


def _wrap_rc(exc: RevenueCatAPIError) -> ToolError:
    return ToolError(f"RevenueCat error: {exc.message}")


async def _load_linked_cred(
    app: App, user_id: int, session: AsyncSession,
) -> RevenueCatCredential | None:
    """Fetch the RC credential linked to the app, if any (ownership-checked)."""
    if app.revenuecat_credential_id is None:
        return None
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    return res.scalar_one_or_none()


async def _resolve_rc_client_for_app(
    app: App, user_id: int, session: AsyncSession,
) -> tuple[RevenueCatClient, RevenueCatCredential]:
    if app.revenuecat_credential_id is None:
        raise ToolError(
            "No RevenueCat credential linked to this app. "
            "Call revenuecat.set_credential first.",
        )
    cred = await _load_linked_cred(app, user_id, session)
    if cred is None:
        raise ToolError("Linked RevenueCat credential not found for this user")
    return RevenueCatClient.from_credential(cred), cred


# ---------------------------------------------------------------------------
# Credential CRUD
# ---------------------------------------------------------------------------


@mcp.tool(name="revenuecat.set_credential")
async def set_credential(
    app_id: int,
    name: str,
    project_id: str,
    secret_key: str,
    rc_app_id: str | None = None,
) -> RevenueCatCredentialResponse:
    """Create or replace the RevenueCat credential linked to an app.

    The ``secret_key`` is encrypted at rest with Fernet. Returns the
    public-safe credential metadata (no secret).
    """
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        cred = await _load_linked_cred(app, user_id, session)

        if cred is None:
            cred = RevenueCatCredential(
                user_id=user_id,
                name=name,
                project_id=project_id,
                rc_app_id=rc_app_id,
                secret_key_encrypted=encrypt_value(secret_key),
            )
            session.add(cred)
            await session.flush()
            app.revenuecat_credential_id = cred.id
        else:
            cred.name = name
            cred.project_id = project_id
            cred.rc_app_id = rc_app_id
            cred.secret_key_encrypted = encrypt_value(secret_key)

        await session.flush()
        return _cred_to_response(cred)


@mcp.tool(name="revenuecat.get_credential")
async def get_credential(app_id: int) -> RevenueCatCredentialResponse | None:
    """Return the RevenueCat credential linked to the app, or ``None``."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        cred = await _load_linked_cred(app, user_id, session)
        return _cred_to_response(cred) if cred else None


@mcp.tool(name="revenuecat.update_credential")
async def update_credential(
    app_id: int,
    name: str | None = None,
    project_id: str | None = None,
    rc_app_id: str | None = None,
    secret_key: str | None = None,
) -> RevenueCatCredentialResponse:
    """Patch fields on the linked RevenueCat credential."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        if app.revenuecat_credential_id is None:
            raise ToolError("No credential to update")
        cred = await _load_linked_cred(app, user_id, session)
        if cred is None:
            raise ToolError("Credential not found")
        if name is not None:
            cred.name = name
        if project_id is not None:
            cred.project_id = project_id
        if rc_app_id is not None:
            cred.rc_app_id = rc_app_id
        if secret_key:
            cred.secret_key_encrypted = encrypt_value(secret_key)
        await session.flush()
        return _cred_to_response(cred)


@mcp.tool(name="revenuecat.delete_credential")
async def delete_credential(app_id: int) -> dict[str, str]:
    """Disconnect the RevenueCat credential from the app and delete it."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        if app.revenuecat_credential_id is None:
            return {"detail": "No credential linked"}
        cred = await _load_linked_cred(app, user_id, session)
        app.revenuecat_credential_id = None
        if cred is not None:
            await session.delete(cred)
        return {"detail": "RevenueCat credential disconnected"}


@mcp.tool(name="revenuecat.test_credential")
async def test_credential(app_id: int) -> RCConnectionTestResponse:
    """Verify the linked RC credential by listing apps in its project."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        try:
            client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        except ToolError as exc:
            return RCConnectionTestResponse(success=False, message=str(exc))

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
                success=False,
                message=f"RevenueCat error: {exc.message}",
            )


# ---------------------------------------------------------------------------
# RC apps + products
# ---------------------------------------------------------------------------


@mcp.tool(name="revenuecat.list_apps")
async def list_rc_apps(app_id: int) -> list[dict[str, Any]]:
    """List RevenueCat apps in the linked project."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.list_apps()
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.list_products")
async def list_products(
    app_id: int,
    store_identifier: str | None = None,
) -> list[dict[str, Any]]:
    """List RevenueCat products attached to the linked RC app.

    ``store_identifier`` filters to a specific store productId (e.g. iOS
    bundle entry).
    """
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, cred = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.list_products(
                    app_id=cred.rc_app_id,
                    store_identifier=store_identifier,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.archive_product")
async def archive_product(app_id: int, rc_product_id: str) -> dict[str, Any]:
    """Archive a RevenueCat product (soft-delete; preserves history)."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.archive_product(rc_product_id)
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


# ---------------------------------------------------------------------------
# Entitlements
# ---------------------------------------------------------------------------


@mcp.tool(name="revenuecat.list_entitlements")
async def list_entitlements(app_id: int) -> list[dict[str, Any]]:
    """List entitlements in the RC project."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.list_entitlements()
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.create_entitlement")
async def create_entitlement(
    app_id: int,
    lookup_key: str,
    display_name: str,
) -> dict[str, Any]:
    """Create a new entitlement (lookup_key + display_name)."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.create_entitlement(
                    lookup_key=lookup_key,
                    display_name=display_name,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.update_entitlement")
async def update_entitlement(
    app_id: int,
    entitlement_id: str,
    display_name: str,
) -> dict[str, Any]:
    """Rename an entitlement's display_name."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.update_entitlement(
                    entitlement_id=entitlement_id,
                    display_name=display_name,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.delete_entitlement")
async def archive_entitlement(
    app_id: int,
    entitlement_id: str,
) -> dict[str, Any]:
    """Archive an entitlement (REST DELETE → archive on RC's side)."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.archive_entitlement(entitlement_id)
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.attach_product_to_entitlement")
async def attach_products_to_entitlement(
    app_id: int,
    entitlement_id: str,
    product_ids: list[str],
) -> dict[str, Any]:
    """Attach RC products to an entitlement."""
    if not product_ids:
        raise ToolError("product_ids cannot be empty")
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.attach_products_to_entitlement(
                    entitlement_id=entitlement_id,
                    product_ids=product_ids,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.detach_product_from_entitlement")
async def detach_products_from_entitlement(
    app_id: int,
    entitlement_id: str,
    product_ids: list[str],
) -> dict[str, Any]:
    """Detach RC products from an entitlement."""
    if not product_ids:
        raise ToolError("product_ids cannot be empty")
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.detach_products_from_entitlement(
                    entitlement_id=entitlement_id,
                    product_ids=product_ids,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


# ---------------------------------------------------------------------------
# Offerings
# ---------------------------------------------------------------------------


@mcp.tool(name="revenuecat.list_offerings")
async def list_offerings(app_id: int) -> list[dict[str, Any]]:
    """List offerings in the RC project."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.list_offerings()
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.create_offering")
async def create_offering(
    app_id: int,
    lookup_key: str,
    display_name: str,
    is_current: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.create_offering(
                    lookup_key=lookup_key,
                    display_name=display_name,
                    is_current=is_current,
                    metadata=metadata,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.update_offering")
async def update_offering(
    app_id: int,
    offering_id: str,
    display_name: str | None = None,
    is_current: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Patch an offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.update_offering(
                    offering_id=offering_id,
                    display_name=display_name,
                    is_current=is_current,
                    metadata=metadata,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.delete_offering")
async def archive_offering(app_id: int, offering_id: str) -> dict[str, Any]:
    """Archive an offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.archive_offering(offering_id)
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


@mcp.tool(name="revenuecat.list_packages")
async def list_packages(app_id: int, offering_id: str) -> list[dict[str, Any]]:
    """List packages inside an offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.list_packages(offering_id)
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.create_package")
async def create_package(
    app_id: int,
    offering_id: str,
    lookup_key: str,
    display_name: str,
    position: int | None = None,
) -> dict[str, Any]:
    """Create a package inside an offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.create_package(
                    offering_id=offering_id,
                    lookup_key=lookup_key,
                    display_name=display_name,
                    position=position,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.delete_package")
async def delete_package(
    app_id: int,
    offering_id: str,
    package_id: str,
) -> dict[str, str]:
    """Delete a package from an offering."""
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                await client.delete_package(offering_id, package_id)
            return {"detail": "Package deleted"}
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.attach_products_to_package")
async def attach_products_to_package(
    app_id: int,
    offering_id: str,
    package_id: str,
    product_ids: list[str],
) -> dict[str, Any]:
    """Attach RC products to a package."""
    if not product_ids:
        raise ToolError("product_ids cannot be empty")
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.attach_products_to_package(
                    offering_id=offering_id,
                    package_id=package_id,
                    product_ids=product_ids,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc


@mcp.tool(name="revenuecat.detach_products_from_package")
async def detach_products_from_package(
    app_id: int,
    offering_id: str,
    package_id: str,
    product_ids: list[str],
) -> dict[str, Any]:
    """Detach RC products from a package."""
    if not product_ids:
        raise ToolError("product_ids cannot be empty")
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
        client, _ = await _resolve_rc_client_for_app(app, user_id, session)
        try:
            async with client:
                return await client.detach_products_from_package(
                    offering_id=offering_id,
                    package_id=package_id,
                    product_ids=product_ids,
                )
        except RevenueCatAPIError as exc:
            raise _wrap_rc(exc) from exc
