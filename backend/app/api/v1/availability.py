"""App Availability API endpoints — toggle per-territory app distribution."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.pricing import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.data.territories import ALPHA2_TO_ALPHA3
from app.db.session import get_session
from app.models.territory import Territory
from app.schemas.availability import (
    AppAvailabilityResponse,
    AppAvailabilityUpdateRequest,
    TerritoryAvailability,
)
from app.services.asc.availability import ASCAvailabilityService
from app.services.asc.errors import ASCAPIError
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()


async def _territory_name_map(session: AsyncSession) -> dict[str, str]:
    """Return alpha-2 → name for all seeded territories."""
    result = await session.execute(select(Territory.code, Territory.name))
    return {row.code: row.name for row in result}


def _build_response(
    raw: dict,
    territory_names: dict[str, str],
) -> AppAvailabilityResponse:
    seen = {t["territory_code"]: t for t in raw["territories"]}
    rows: list[TerritoryAvailability] = []
    # Include every seeded territory so the UI can show them all even
    # when Apple hasn't yet stamped a row for one (treat missing as off).
    for alpha2, name in sorted(territory_names.items()):
        entry = seen.get(alpha2, {"available": False, "preorder_enabled": False})
        rows.append(
            TerritoryAvailability(
                territory_code=alpha2,
                territory_name=name,
                available=bool(entry.get("available", False)),
                preorder_enabled=bool(entry.get("preorder_enabled", False)),
            )
        )
    return AppAvailabilityResponse(
        available_in_new_territories=bool(raw["available_in_new_territories"]),
        territories=rows,
    )


@router.get(
    "/{app_id}/availability",
    response_model=AppAvailabilityResponse,
)
async def get_availability(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppAvailabilityResponse:
    """Return current per-territory availability fetched from Apple."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if not app.asc_app_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="App not yet synced from App Store Connect.",
        )
    territory_names = await _territory_name_map(session)

    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCAvailabilityService(client)
        try:
            raw = await service.get_app_availability(app.asc_app_id)
        except ASCAPIError as exc:
            logger.warning(
                "ASC rejected availability fetch for app_id=%s: %s",
                app_id, exc.message,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="App Store Connect rejected the availability request.",
            )

    return _build_response(raw, territory_names)


@router.put(
    "/{app_id}/availability",
    response_model=AppAvailabilityResponse,
)
async def update_availability(
    app_id: int,
    body: AppAvailabilityUpdateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppAvailabilityResponse:
    """Submit a new availability snapshot to Apple, then return the result."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if not app.asc_app_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="App not yet synced from App Store Connect.",
        )
    territory_names = await _territory_name_map(session)

    disabled = {code.upper() for code in body.disabled_territories}
    unknown = disabled - ALPHA2_TO_ALPHA3.keys()
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown territory codes: {sorted(unknown)}",
        )

    available_codes = sorted(ALPHA2_TO_ALPHA3.keys() - disabled)
    if not available_codes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refusing to make app globally unavailable.",
        )

    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCAvailabilityService(client)
        try:
            await service.set_app_availability(
                app.asc_app_id,
                available_codes,
                body.available_in_new_territories,
            )
            raw = await service.get_app_availability(app.asc_app_id)
        except ASCAPIError as exc:
            logger.warning(
                "ASC rejected availability update for app_id=%s: %s",
                app_id, exc.message,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="App Store Connect rejected the availability request.",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    return _build_response(raw, territory_names)
