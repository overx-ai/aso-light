"""Per-app screenshot comparison endpoint.

Mounted under /apps so the auth chain runs through ``_get_verified_app``
identically to other per-app routers (pricing, metadata, asa, etc.). Serves
the composited BEFORE/AFTER screenshot montage (live default page vs a
Custom Product Page) as ``image/png`` for the React Compare page.

Also exposes the live DEFAULT-page screenshots as JSON (slot index +
``source_url``) so the in-browser Compare page can show a slot-by-slot
before/after without rendering a server-side montage or requiring a Custom
Product Page.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.cpp import is_valid_display_type
from app.services.asc.cpp import ASCCustomProductPageService
from app.services.asc.errors import ASCAPIError
from app.services.visual.compare import build_comparison

logger = logging.getLogger(__name__)
router = APIRouter()


def _validate_display_type(display_type: str) -> None:
    """Reject unknown ``screenshotDisplayType`` values with a 400."""
    if not is_valid_display_type(display_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown display_type '{display_type}'.",
        )


@router.get("/{app_id}/screenshots/compare")
async def compare_screenshots(
    app_id: int,
    cpp_id: str = Query(..., description="Custom Product Page id (the 'after' side)"),
    locale: str = Query(..., description="App Store locale, e.g. en-US"),
    display_type: str = Query(
        ...,
        description="Apple screenshotDisplayType, e.g. APP_IPHONE_67",
    ),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return a composited BEFORE/AFTER screenshot montage as a PNG.

    "Before" is the live DEFAULT product page's screenshots; "after" is the
    selected Custom Product Page's screenshots — both resolved for the given
    ``locale`` + ``display_type`` (device family).
    """
    _validate_display_type(display_type)
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ASC API error: {exc.message}",
            ) from exc
        except Exception:
            logger.exception(
                "compare montage build failed for app=%s cpp=%s", app_id, cpp_id
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not build the comparison montage.",
            )
    return Response(content=png_bytes, media_type="image/png")


@router.get("/{app_id}/screenshots/default")
async def default_screenshots(
    app_id: int,
    locale: str = Query(..., description="App Store locale, e.g. en-US"),
    display_type: str = Query(
        ...,
        description="Apple screenshotDisplayType, e.g. APP_IPHONE_67",
    ),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the live DEFAULT product page's screenshots as JSON.

    The in-browser Compare page uses this as the "before (current)" side: a
    slot-by-slot list of ``{slot, source_url, file_name}`` for the requested
    ``locale`` + ``display_type``. Slots are ordered by Apple's screenshot
    order and re-indexed 1..N.

    When App Store Connect has no live version/localization for the locale —
    or no credentials are usable — the list is empty (HTTP 200), so the UI
    falls back to a manual "before" upload rather than surfacing an error.
    """
    _validate_display_type(display_type)
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCCustomProductPageService(client)
        try:
            version_localization_id = (
                await service.get_default_version_localization_id(
                    app.asc_app_id, locale
                )
            )
            if version_localization_id is None:
                return {"items": []}

            sets = await service.get_default_screenshots(
                version_localization_id
            )
        except ASCAPIError as exc:
            logger.warning(
                "default_screenshots: ASC error for app=%s locale=%s: %s",
                app_id,
                locale,
                exc.message,
            )
            return {"items": []}

    items: list[dict[str, Any]] = []
    slot = 0
    for set_obj in sets:
        if set_obj.get("display_type") != display_type:
            continue
        for shot in set_obj.get("screenshots", []):
            source_url = shot.get("source_url")
            if not source_url:
                continue
            slot += 1
            items.append(
                {
                    "slot": slot,
                    "source_url": source_url,
                    "file_name": shot.get("file_name"),
                }
            )

    return {"items": items}
