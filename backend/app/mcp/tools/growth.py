"""MCP tool for the Growth Advisor.

Thin wrapper over ``GET /apps/{id}/growth/recommendations`` — same service,
same ownership chain via :func:`resolve_app`.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.api.v1.growth import get_growth_recommendations
from app.mcp.context import (
    current_user_claims,
    http_to_tool_error,
    resolve_app,
    session_scope,
)
from app.mcp.server import mcp
from app.schemas.growth import GrowthRecommendationsOut


@mcp.tool(name="growth_recommendations")
async def growth_recommendations(app_id: int) -> GrowthRecommendationsOut:
    """Ranked growth recommendations for an app (pricing gaps, ASO gaps)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        try:
            return await get_growth_recommendations(
                app_id=app.id,
                current_user=current_user_claims(),
                session=session,
            )
        except HTTPException as exc:
            raise http_to_tool_error(exc) from exc
