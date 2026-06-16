"""App Clash — side-by-side comparison of an app and its competitors via iTunes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.competitor import CompetitorApp
from app.schemas.clash import AppClashOut, ClashRow
from app.services.clash import build_row
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{app_id}/clash", response_model=AppClashOut)
async def app_clash(
    app_id: int,
    country: str = Query(default="us", min_length=2, max_length=2),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppClashOut:
    """Side-by-side iTunes lookup for our app + every saved competitor.

    Pure read — calls iTunes Lookup once with all ids batched, then maps the
    response into a flat ``ClashRow`` list. Missing competitors (deleted from
    the App Store, wrong storefront) are emitted with whatever data we have
    locally so the UI can still render them.
    """
    user_id = int(current_user["user_id"])
    country = country.lower()
    app = await _get_verified_app(app_id, user_id, session)

    competitors_result = await session.execute(
        select(CompetitorApp).where(CompetitorApp.app_id == app_id)
    )
    competitors = list(competitors_result.scalars().all())

    track_ids: list[str] = []
    if app.asc_app_id:
        track_ids.append(str(app.asc_app_id))
    for c in competitors:
        if c.asc_app_id:
            track_ids.append(str(c.asc_app_id))

    svc = ITunesSearchService()
    lookup = await svc.lookup_apps(track_ids, country=country)
    by_id = {str(item.get("trackId")): item for item in lookup}

    rows: list[ClashRow] = []
    if app.asc_app_id:
        asc_id = str(app.asc_app_id)
        rows.append(
            build_row(
                by_id.get(asc_id),
                is_self=True,
                asc_app_id=asc_id,
                fallback_name=app.name,
                fallback_bundle=app.bundle_id,
            )
        )

    for c in competitors:
        asc_id = str(c.asc_app_id)
        rows.append(
            build_row(
                by_id.get(asc_id),
                is_self=False,
                asc_app_id=asc_id,
                fallback_name=c.name,
                fallback_bundle=c.bundle_id,
            )
        )

    return AppClashOut(country=country, rows=rows)
