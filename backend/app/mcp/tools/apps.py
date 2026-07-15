"""MCP tools for App management — list, get, sync from ASC."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.mcp.context import (
    get_user_id,
    resolve_app,
    resolve_credential,
    session_scope,
)
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.schemas.app import AppResponse, AppSyncResponse
from app.services.asc.apps import sync_apps_for_credentials
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)


async def _user_credential_ids(session, user_id: int) -> list[int]:
    res = await session.execute(
        select(ASCCredential.id).where(ASCCredential.user_id == user_id)
    )
    return list(res.scalars().all())


@mcp.tool(name="apps_list")
async def list_apps_tool() -> list[AppResponse]:
    """List every App owned by the authenticated user (across all credentials)."""
    async with session_scope() as session:
        user_id = get_user_id()
        cred_ids = await _user_credential_ids(session, user_id)
        if not cred_ids:
            return []
        res = await session.execute(
            select(App).where(App.credential_id.in_(cred_ids))
        )
        return [AppResponse.model_validate(a) for a in res.scalars().all()]


@mcp.tool(name="apps_get")
async def get_app_tool(app_id: int) -> AppResponse:
    """Fetch a single App by id, verifying user ownership."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        return AppResponse.model_validate(app)


@mcp.tool(name="apps_sync")
async def sync_apps_tool(credential_id: int | None = None) -> AppSyncResponse:
    """Pull the live app list from ASC for the user's credentials and upsert.

    If ``credential_id`` is provided, only that credential is synced (after
    verifying ownership). Otherwise every credential the user owns is synced.
    """
    async with session_scope() as session:
        user_id = get_user_id()

        if credential_id is not None:
            cred = await resolve_credential(credential_id, session)
            credentials = [cred]
        else:
            res = await session.execute(
                select(ASCCredential).where(ASCCredential.user_id == user_id)
            )
            credentials = list(res.scalars().all())

        if not credentials:
            return AppSyncResponse(synced=0, apps=[])

        synced_apps = await sync_apps_for_credentials(session, credentials)
        if synced_apps:
            await ITunesSearchService().backfill_icons(synced_apps)
            await session.flush()

        return AppSyncResponse(
            synced=len(synced_apps),
            apps=[AppResponse.model_validate(a) for a in synced_apps],
        )
