"""MCP tools for app territory availability — get + update via ASC."""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.territories import ALPHA2_TO_ALPHA3
from app.mcp.context import resolve_app, resolve_asc_client, session_scope
from app.mcp.server import mcp
from app.models.territory import Territory
from app.schemas.availability import (
    AppAvailabilityResponse,
    AppAvailabilityUpdateRequest,
    TerritoryAvailability,
)
from app.services.asc.availability import ASCAvailabilityService
from app.services.asc.errors import ASCAPIError


async def _territory_name_map(session: AsyncSession) -> dict[str, str]:
    res = await session.execute(select(Territory.code, Territory.name))
    return {row.code: row.name for row in res}


def _build_response(
    raw: dict, territory_names: dict[str, str]
) -> AppAvailabilityResponse:
    seen = {t["territory_code"]: t for t in raw["territories"]}
    rows: list[TerritoryAvailability] = []
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


@mcp.tool(name="availability.get")
async def get_availability_tool(app_id: int) -> AppAvailabilityResponse:
    """Fetch current per-territory availability for an app from Apple."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        territory_names = await _territory_name_map(session)
        client = await resolve_asc_client(app, session)
        async with client:
            service = ASCAvailabilityService(client)
            try:
                raw = await service.get_app_availability(app.asc_app_id)
            except ASCAPIError as exc:
                raise ToolError(f"Apple rejected availability fetch: {exc}")
        return _build_response(raw, territory_names)


@mcp.tool(name="availability.update")
async def update_availability_tool(
    app_id: int,
    body: AppAvailabilityUpdateRequest,
) -> AppAvailabilityResponse:
    """Submit a new availability snapshot to Apple, then return the result.

    ``disabled_territories`` is an alpha-2 list of territories the user wants
    OFF; everything else stays available. Refuses to make the app globally
    unavailable.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        territory_names = await _territory_name_map(session)

        disabled = {code.upper() for code in body.disabled_territories}
        unknown = disabled - ALPHA2_TO_ALPHA3.keys()
        if unknown:
            raise ToolError(f"Unknown territory codes: {sorted(unknown)}")

        available_codes = sorted(ALPHA2_TO_ALPHA3.keys() - disabled)
        if not available_codes:
            raise ToolError("Refusing to make app globally unavailable.")

        client = await resolve_asc_client(app, session)
        async with client:
            service = ASCAvailabilityService(client)
            try:
                await service.set_app_availability(
                    app.asc_app_id,
                    available_codes,
                    body.available_in_new_territories,
                )
                raw = await service.get_app_availability(app.asc_app_id)
            except ASCAPIError as exc:
                raise ToolError(f"Apple rejected availability update: {exc}")
            except ValueError as exc:
                raise ToolError(str(exc))

        return _build_response(raw, territory_names)
