"""MCP tools for the static App Store territory catalog."""

from __future__ import annotations

from sqlalchemy import select

from app.mcp.context import session_scope
from app.mcp.server import mcp
from app.models.territory import Territory
from app.schemas.territory import TerritoryResponse


@mcp.tool(name="territories_list")
async def list_territories_tool() -> list[TerritoryResponse]:
    """List all seeded App Store territories with currency + VAT rate info."""
    async with session_scope() as session:
        res = await session.execute(select(Territory).order_by(Territory.code))
        return [TerritoryResponse.model_validate(t) for t in res.scalars().all()]
