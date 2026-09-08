"""Per-app App Store analytics endpoints.

Mounted under /apps so ownership runs through ``_get_verified_app`` exactly
like every other per-app router. Reads come from the local fact table; pulling
from Apple is always an explicit ``POST .../sync``, matching the pricing and
metadata conventions.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.services import analytics as analytics_service
from app.services.asc.errors import ASCAPIError

router = APIRouter()


def _http_error(exc: ASCAPIError) -> HTTPException:
    """Translate an ASC failure without leaking raw exception text.

    A 403 on enrollment is the common, actionable case: Apple requires an
    Admin key to request a report type for the first time.
    """
    if exc.status_code == 403:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "App Store Connect rejected the request. Enrolling an app for "
                "analytics reports requires a key with the Admin role."
            ),
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"App Store Connect error: {exc.message}",
    )


@router.post("/{app_id}/analytics/enroll")
async def enroll(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enroll the app for snapshot + ongoing analytics reports.

    Idempotent — an existing enrollment is reused rather than duplicated.
    Ongoing reports produce no data for 24-48h; the snapshot carries history.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client = await _get_asc_client_for_app(app, session)
    try:
        return await analytics_service.enroll_app(session, app=app, client=client)
    except ASCAPIError as exc:
        raise _http_error(exc) from exc
    finally:
        await client.close()


@router.get("/{app_id}/analytics/status")
async def status_(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enrollment state and how much data is stored locally."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    return await analytics_service.enrollment_status(
        session, user_id=user_id, app_id=app.id
    )


@router.post("/{app_id}/analytics/sync")
async def sync(
    app_id: int,
    days: int = Query(30, ge=1, le=365),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Pull new report instances from Apple and upsert them."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    client = await _get_asc_client_for_app(app, session)
    try:
        return await analytics_service.sync_app_analytics(
            session, app=app, client=client, days=days
        )
    except ASCAPIError as exc:
        raise _http_error(exc) from exc
    finally:
        await client.close()


@router.get("/{app_id}/analytics/engagement")
async def engagement(
    app_id: int,
    days: int = Query(30, ge=1, le=365),
    territory: str | None = None,
    source_type: str | None = None,
    page_title: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Daily impressions, product page views and taps."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff, rows = await analytics_service.engagement_rows(
        session=session,
        user_id=user_id,
        app_id=app.id,
        days=days,
        territory=territory,
        source_type=source_type,
        page_title=page_title,
    )
    return {"since": cutoff, "rows": rows}


@router.get("/{app_id}/analytics/downloads")
async def downloads(
    app_id: int,
    days: int = Query(30, ge=1, le=365),
    territory: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Daily downloads and redownloads."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff, rows = await analytics_service.download_rows(
        session=session,
        user_id=user_id,
        app_id=app.id,
        days=days,
        territory=territory,
    )
    return {"since": cutoff, "rows": rows}


@router.get("/{app_id}/analytics/cpp")
async def cpp(
    app_id: int,
    days: int = Query(30, ge=1, le=365),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Per-Custom-Product-Page rollup with a derived conversion rate.

    ``conversion_rate`` is null when a page had no views — unknown, not zero.
    Figures reflect Apple's privacy thresholding and will not reconcile exactly
    with Apple Search Ads or the App Store Connect UI.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff, rows = await analytics_service.cpp_performance(
        session=session, user_id=user_id, app_id=app.id, days=days
    )
    return {"since": cutoff, "rows": rows}
