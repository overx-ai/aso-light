"""MCP tools for App Store analytics — impressions, page views, CPP, downloads.

Thin wrappers over :mod:`app.services.analytics`. Each tool resolves the local
``app_id`` through ``resolve_app`` (enforcing the
``app.credential_id -> credential.user_id`` chain) and calls the service
directly — no HTTP hop.

Reads come from the local fact table; ``analytics_sync`` is the only tool that
talks to Apple, matching the explicit-sync convention used everywhere else.

Tool names are underscored: the Anthropic tool-name regex is
``^[a-zA-Z0-9_-]{1,64}$`` and a dotted name breaks Claude Desktop. Enforced by
tests/test_mcp_tool_names.py.
"""
from __future__ import annotations

import logging
from typing import Any

from fastmcp.exceptions import ToolError

from app.mcp.context import (
    get_user_id,
    resolve_app,
    resolve_asc_client,
    session_scope,
)
from app.mcp.server import mcp
from app.services import analytics as analytics_service
from app.services.asc.errors import ASCAPIError

logger = logging.getLogger(__name__)


def _tool_error(exc: ASCAPIError) -> ToolError:
    if exc.status_code == 403:
        return ToolError(
            "App Store Connect rejected the request. Enrolling an app for "
            "analytics reports requires a key with the Admin role."
        )
    return ToolError(f"App Store Connect error: {exc.message}")


@mcp.tool(name="analytics_enroll")
async def analytics_enroll(app_id: int) -> dict[str, Any]:
    """Enroll an app for App Store analytics reports (snapshot + ongoing).

    Idempotent. Requires an Admin ASC key the first time a report type is
    requested. Ongoing reports deliver nothing for 24-48h; the one-time
    snapshot is what returns historical data immediately.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        try:
            return await analytics_service.enroll_app(
                session, app=app, client=client
            )
        except ASCAPIError as exc:
            raise _tool_error(exc) from exc
        finally:
            await client.close()


@mcp.tool(name="analytics_status")
async def analytics_status(app_id: int) -> dict[str, Any]:
    """Report whether an app is enrolled and what data is stored locally."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        return await analytics_service.enrollment_status(
            session, user_id=get_user_id(), app_id=app.id
        )


@mcp.tool(name="analytics_sync")
async def analytics_sync(app_id: int, days: int = 30) -> dict[str, Any]:
    """Pull new analytics report instances from Apple into the local cache."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        try:
            return await analytics_service.sync_app_analytics(
                session, app=app, client=client, days=days
            )
        except ASCAPIError as exc:
            raise _tool_error(exc) from exc
        finally:
            await client.close()


@mcp.tool(name="analytics_engagement")
async def analytics_engagement(
    app_id: int,
    days: int = 30,
    territory: str | None = None,
    source_type: str | None = None,
    page_title: str | None = None,
) -> dict[str, Any]:
    """Daily impressions, product page views and taps for an app.

    Figures reflect Apple's privacy thresholding (rows under 5 devices are
    dropped, and noise is added), so they will not match Apple Search Ads or
    the App Store Connect UI exactly.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        cutoff, rows = await analytics_service.engagement_rows(
            session=session,
            user_id=get_user_id(),
            app_id=app.id,
            days=days,
            territory=territory,
            source_type=source_type,
            page_title=page_title,
        )
        return {"since": str(cutoff), "rows": rows}


@mcp.tool(name="analytics_downloads")
async def analytics_downloads(
    app_id: int, days: int = 30, territory: str | None = None,
) -> dict[str, Any]:
    """Daily downloads and redownloads for an app."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        cutoff, rows = await analytics_service.download_rows(
            session=session,
            user_id=get_user_id(),
            app_id=app.id,
            days=days,
            territory=territory,
        )
        return {"since": str(cutoff), "rows": rows}


@mcp.tool(name="analytics_cpp_performance")
async def analytics_cpp_performance(
    app_id: int, days: int = 30,
) -> dict[str, Any]:
    """Per-Custom-Product-Page impressions, views, downloads and conversion.

    ``page_title`` is Apple's name for the product page shown
    ("Default product page", or a custom product page's own title). ``conversion_rate`` is
    null when a page had no views — unknown, not zero.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        cutoff, rows = await analytics_service.cpp_performance(
            session=session,
            user_id=get_user_id(),
            app_id=app.id,
            days=days,
        )
        return {
            "since": str(cutoff),
            "rows": [
                {
                    **row,
                    "conversion_rate": (
                        str(row["conversion_rate"])
                        if row["conversion_rate"] is not None
                        else None
                    ),
                }
                for row in rows
            ],
        }
