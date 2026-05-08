"""MCP tools for App Clash — side-by-side competitor comparison via iTunes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.competitor import CompetitorApp
from app.schemas.clash import AppClashOut, ClashRow
from app.services.keywords.itunes_search import ITunesSearchService

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


def _build_row(
    raw: dict[str, Any] | None,
    *,
    is_self: bool,
    asc_app_id: str,
    fallback_name: str | None,
    fallback_bundle: str | None,
) -> ClashRow:
    if not raw:
        return ClashRow(
            track_id=asc_app_id,
            is_self=is_self,
            name=fallback_name,
            bundle_id=fallback_bundle,
        )
    return ClashRow(
        track_id=str(raw.get("trackId") or ""),
        is_self=is_self,
        name=raw.get("trackName") or fallback_name,
        subtitle=None,
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


@mcp.tool(name="clash.run")
async def app_clash_tool(app_id: int, country: str = "us") -> AppClashOut:
    """Side-by-side iTunes lookup for our app + every saved competitor.

    Pure read — calls iTunes Lookup once with all ids batched, then maps the
    response into a flat ``ClashRow`` list. Missing competitors are emitted
    with whatever data we have locally so the UI can still render them.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

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
                _build_row(
                    by_id.get(asc_id),
                    is_self=False,
                    asc_app_id=asc_id,
                    fallback_name=c.name,
                    fallback_bundle=c.bundle_id,
                )
            )

        return AppClashOut(country=country.lower(), rows=rows)
