"""MCP tools for App Clash — side-by-side competitor comparison via iTunes."""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.competitor import CompetitorApp
from app.schemas.clash import AppClashOut, ClashRow
from app.services.clash import build_row
from app.services.keywords.itunes_search import ITunesSearchService


@mcp.tool(name="clash.run")
async def app_clash_tool(app_id: int, country: str = "us") -> AppClashOut:
    """Side-by-side iTunes lookup for our app + every saved competitor.

    Pure read — calls iTunes Lookup once with all ids batched, then maps the
    response into a flat ``ClashRow`` list. Missing competitors are emitted
    with whatever data we have locally so the UI can still render them.
    """
    country = country.strip().lower()
    if len(country) != 2:
        raise ToolError("country must be a 2-letter code")

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
