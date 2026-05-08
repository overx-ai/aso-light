"""Per-tool helpers for resolving the authenticated user and an owned App.

These mirror the dependencies used by the REST routers (``get_current_user``
and ``_get_verified_app``) so MCP tools enforce the same
``app.credential_id -> credential.user_id`` chain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.db.session import async_session_factory
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.iap import InAppPurchase
from app.models.revenuecat_credential import RevenueCatCredential
from app.models.subscription import Subscription, SubscriptionGroup
from app.services.asc.errors import CredentialDecryptError


def _get_token_claim_as_int(claim_name: str) -> int:
    """Read an integer claim from the current MCP access token."""
    token = get_access_token()
    if token is None:
        raise ToolError("Not authenticated")
    claim_value = token.claims.get(claim_name)
    if claim_value is None:
        raise ToolError(f"Token missing {claim_name} claim")
    return int(claim_value)


def get_user_id() -> int:
    """Read the authenticated user_id from the current MCP request."""
    return _get_token_claim_as_int("user_id")


def get_pat_id() -> int:
    """Read the authenticated personal-access-token id from the current MCP request."""
    return _get_token_claim_as_int("pat_id")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a fresh DB session, committing on success."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _http_to_tool_error(exc: HTTPException) -> ToolError:
    """Convert an HTTPException raised by a REST helper into a ToolError.

    Only the ``detail`` is forwarded — the HTTP status code framing is
    irrelevant to an MCP client.
    """
    return ToolError(str(exc.detail))


async def resolve_app(app_id: int, session: AsyncSession) -> App:
    """Load an :class:`App` and verify the current user owns its credential."""
    user_id = get_user_id()
    try:
        return await _get_verified_app(app_id, user_id, session)
    except HTTPException as exc:
        raise _http_to_tool_error(exc) from exc


async def resolve_asc_client(app: App, session: AsyncSession):
    """Build an ASCClient for an app already verified by :func:`resolve_app`.

    Surfaces credential-decrypt failures as clean :class:`ToolError`s so MCP
    clients see a single-line "re-upload your .p8" message instead of a deep
    PyJWT/PEM traceback.
    """
    try:
        return await _get_asc_client_for_app(app, session)
    except CredentialDecryptError as exc:
        raise ToolError(str(exc)) from exc
    except HTTPException as exc:
        raise _http_to_tool_error(exc) from exc


async def resolve_rc_credential(
    app: App, session: AsyncSession,
) -> RevenueCatCredential | None:
    if app.revenuecat_credential_id is None:
        return None
    user_id = get_user_id()
    res = await session.execute(
        select(RevenueCatCredential).where(
            RevenueCatCredential.id == app.revenuecat_credential_id,
            RevenueCatCredential.user_id == user_id,
        )
    )
    return res.scalar_one_or_none()


async def resolve_credential(credential_id: int, session: AsyncSession) -> ASCCredential:
    user_id = get_user_id()
    res = await session.execute(
        select(ASCCredential).where(
            ASCCredential.id == credential_id,
            ASCCredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise ToolError("Credential not found or not owned by user")
    return cred


async def resolve_subscription(
    subscription_id: int, app_id: int, session: AsyncSession,
) -> Subscription:
    """Load a Subscription and verify it belongs to the given (already-verified) app."""
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
        raise ToolError("Subscription not found for this app")
    return sub


async def resolve_subscription_group(
    group_id: int, app_id: int, session: AsyncSession,
) -> SubscriptionGroup:
    """Load a SubscriptionGroup and verify it belongs to the given app."""
    res = await session.execute(
        select(SubscriptionGroup).where(
            SubscriptionGroup.id == group_id,
            SubscriptionGroup.app_id == app_id,
        )
    )
    group = res.scalar_one_or_none()
    if group is None:
        raise ToolError("Subscription group not found for this app")
    return group


async def resolve_iap(
    iap_id: int, app_id: int, session: AsyncSession,
) -> InAppPurchase:
    """Load an IAP and verify it belongs to the given app."""
    res = await session.execute(
        select(InAppPurchase).where(
            InAppPurchase.id == iap_id,
            InAppPurchase.app_id == app_id,
        )
    )
    iap = res.scalar_one_or_none()
    if iap is None:
        raise ToolError("In-app purchase not found for this app")
    return iap
