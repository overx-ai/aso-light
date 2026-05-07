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
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)
router = APIRouter()


_DESCRIPTION_EXCERPT_LEN = 280


def _file_size_mb(raw_size: Any) -> float | None:
    if not isinstance(raw_size, (int, str)):
        return None
    try:
        return round(int(raw_size) / (1024 * 1024), 1)
    except (TypeError, ValueError):
        return None


def _description_excerpt(description: str | None) -> str | None:
    if not description:
        return None
    if len(description) <= _DESCRIPTION_EXCERPT_LEN:
        return description
    return description[:_DESCRIPTION_EXCERPT_LEN].rstrip() + "…"


def _row_from_lookup(
    raw: dict[str, Any],
    *,
    is_self: bool,
    fallback_name: str | None = None,
    fallback_bundle: str | None = None,
) -> ClashRow:
    return ClashRow(
        track_id=str(raw.get("trackId") or ""),
        is_self=is_self,
        name=raw.get("trackName") or fallback_name,
        subtitle=None,  # iTunes lookup doesn't return subtitles for storefront listings
        seller=raw.get("sellerName"),
        primary_genre=raw.get("primaryGenreName"),
        average_rating=raw.get("averageUserRating"),
        rating_count=raw.get("userRatingCount"),
        release_date=raw.get("releaseDate"),
        version=raw.get("version"),
        file_size_mb=_file_size_mb(raw.get("fileSizeBytes")),
        price=raw.get("price"),
        currency=raw.get("currency"),
        formatted_price=raw.get("formattedPrice"),
        icon_url=raw.get("artworkUrl100"),
        bundle_id=raw.get("bundleId") or fallback_bundle,
        description_excerpt=_description_excerpt(raw.get("description")),
    )


def _build_row(
    raw: dict[str, Any] | None,
    *,
    is_self: bool,
    asc_app_id: str,
    fallback_name: str | None,
    fallback_bundle: str | None,
) -> ClashRow:
    """Build a ClashRow either from an iTunes lookup result or from the
    locally-known fallback fields when the storefront has no record."""
    if raw:
        return _row_from_lookup(
            raw,
            is_self=is_self,
            fallback_name=fallback_name,
            fallback_bundle=fallback_bundle,
        )
    return ClashRow(
        track_id=asc_app_id,
        is_self=is_self,
        name=fallback_name,
        bundle_id=fallback_bundle,
    )


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
    lookup = await svc.lookup_apps(track_ids, country=country.lower())
    by_id = {str(item.get("trackId")): item for item in lookup}

    rows: list[ClashRow] = []
    if app.asc_app_id:
        asc_id = str(app.asc_app_id)
        rows.append(
            _build_row(
                by_id.get(asc_id) or None,
                is_self=True,
                asc_app_id=asc_id,
                fallback_name=app.name,
                fallback_bundle=app.bundle_id,
            )
        )

    for c in competitors:
        asc_id = str(c.asc_app_id)
        rows.append(
            _build_row(
                by_id.get(asc_id) or None,
                is_self=False,
                asc_app_id=asc_id,
                fallback_name=c.name,
                fallback_bundle=c.bundle_id,
            )
        )

    return AppClashOut(country=country.lower(), rows=rows)
