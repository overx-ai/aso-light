"""Shared FastAPI helpers for v1 routers.

These helpers centralize the ASC-ownership check so that every router that
operates on an :class:`App` enforces the same invariant:

    app.credential_id -> credential.user_id == current_user_id

Without this guard, a user could read or mutate another user's apps simply
by knowing the numeric primary key.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.credential import ASCCredential
from app.services.asc.client import ASCClient
from app.services.asc.errors import CredentialDecryptError


async def _get_verified_app(
    app_id: int,
    user_id: int,
    session: AsyncSession,
) -> App:
    """Load an App record and verify that it belongs to the current user.

    Verifies the chain ``app.credential_id -> credential.user_id == user_id``.

    Raises:
        HTTPException 404: if the app does not exist.
        HTTPException 403: if the app exists but is not owned by the user.
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
    """Build an :class:`ASCClient` from the credential that owns the given app.

    The .p8 private key stored on the credential is decrypted in memory by
    :meth:`ASCClient.from_credential` and is never persisted in cleartext.
    """
    result = await session.execute(
        select(ASCCredential).where(ASCCredential.id == app.credential_id)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Credential for app not found",
        )
    try:
        return ASCClient.from_credential(credential)
    except CredentialDecryptError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
