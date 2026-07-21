"""MCP tools for the visual old-vs-new screenshot comparison.

``screenshots.compare`` resolves a live DEFAULT product page's screenshots
(the "before") and a Custom Product Page's screenshots (the "after") for a
locale + device family, composites them into a labeled two-row montage via
:func:`app.services.visual.compare.build_comparison`, and returns it as a
FastMCP :class:`Image` so MCP clients can view the creative inline.
"""
from __future__ import annotations

import logging

from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from app.api.v1._deps import _get_asc_client_for_app
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.schemas.cpp import is_valid_display_type
from app.services.asc.errors import ASCAPIError
from app.services.visual.compare import build_comparison

logger = logging.getLogger(__name__)


@mcp.tool(name="screenshots_compare")
async def compare_screenshots(
    app_id: int,
    cpp_id: str,
    locale: str,
    display_type: str,
) -> Image:
    """Composite a BEFORE/AFTER screenshot montage for a CPP vs the default page.

    Args:
        app_id: The local app id.
        cpp_id: The Custom Product Page id whose screenshots are the
            "after" (new) side of the comparison.
        locale: The App Store locale (e.g. ``en-US``).
        display_type: Apple's ``screenshotDisplayType`` (e.g.
            ``APP_IPHONE_67``) selecting the device family.

    Returns:
        A FastMCP :class:`Image` wrapping the composited PNG.
    """
    if not is_valid_display_type(display_type):
        raise ToolError(f"Unknown display_type '{display_type}'.")
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            try:
                png_bytes = await build_comparison(
                    client,
                    app.asc_app_id,
                    cpp_id,
                    locale,
                    display_type,
                )
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")
            except Exception:
                logger.exception(
                    "compare montage build failed for app=%s cpp=%s",
                    app_id,
                    cpp_id,
                )
                raise ToolError("Could not build the comparison montage.")
    return Image(data=png_bytes, format="png")
