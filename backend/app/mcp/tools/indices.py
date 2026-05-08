"""MCP tools for economic-index status, refresh, and GDP listing."""

from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError
from sqlalchemy import func, select

from app.mcp.context import session_scope
from app.mcp.server import mcp
from app.models.economic_index import EconomicIndex
from app.models.territory import Territory
from app.services.indices.refresh import IndexRefreshService

VALID_INDEX_TYPES = {"ppp", "bigmac", "netflix", "spotify", "gdp_per_capita_ppp"}
GDP_INDEX_TYPE = "gdp_per_capita_ppp"


@mcp.tool(name="indices.status")
async def index_status_tool() -> dict[str, Any]:
    """Return last refresh timestamps and record counts per index type."""
    async with session_scope() as session:
        res = await session.execute(
            select(
                EconomicIndex.index_type,
                func.count(EconomicIndex.id).label("count"),
                func.max(EconomicIndex.updated_at).label("last_updated"),
                func.max(EconomicIndex.reference_date).label("latest_reference_date"),
            ).group_by(EconomicIndex.index_type)
        )
        statuses: dict[str, dict[str, Any]] = {
            idx_type: {
                "count": 0,
                "last_updated": None,
                "latest_reference_date": None,
            }
            for idx_type in VALID_INDEX_TYPES
        }
        for row in res.all():
            statuses[row.index_type] = {
                "count": row.count,
                "last_updated": (
                    row.last_updated.isoformat() if row.last_updated else None
                ),
                "latest_reference_date": (
                    row.latest_reference_date.isoformat()
                    if row.latest_reference_date
                    else None
                ),
            }
        return {"indices": statuses}


@mcp.tool(name="indices.refresh")
async def refresh_indices_tool(index_type: str | None = None) -> dict[str, Any]:
    """Trigger an economic-index refresh.

    Pass ``index_type`` to refresh just one of: ``ppp``, ``bigmac``,
    ``netflix``, ``spotify``, ``gdp_per_capita_ppp``. Omit it to refresh all.
    """
    async with session_scope() as session:
        service = IndexRefreshService(session)
        if index_type is not None:
            if index_type not in VALID_INDEX_TYPES:
                raise ToolError(
                    f"Invalid index type: {index_type}. "
                    f"Valid types: {', '.join(sorted(VALID_INDEX_TYPES))}"
                )
            count = await service.refresh_type(index_type)
            return {"refreshed": {index_type: count}}

        results = await service.refresh_all()
        return {"refreshed": results}


@mcp.tool(name="indices.list_gdp")
async def list_gdp_tool() -> list[dict[str, Any]]:
    """Return GDP/capita PPP per territory, sorted descending.

    Powers the GDP-bracket UI. Territories without GDP data are included with
    a null value so the full set is visible.
    """
    async with session_scope() as session:
        rows = await session.execute(
            select(
                Territory.code,
                Territory.name,
                Territory.currency_code,
                EconomicIndex.value,
            )
            .outerjoin(
                EconomicIndex,
                (EconomicIndex.territory_id == Territory.id)
                & (EconomicIndex.index_type == GDP_INDEX_TYPE),
            )
            .order_by(EconomicIndex.value.desc().nullslast(), Territory.name)
        )
        return [
            {
                "territory_code": row.code,
                "territory_name": row.name,
                "currency_code": row.currency_code,
                "gdp_per_capita_ppp": row.value,
            }
            for row in rows
        ]
