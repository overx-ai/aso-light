"""Growth Advisor endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.growth import (
    GrowthRecommendationsOut,
    GrowthRecommendationSummary,
)
from app.services.growth.recommendations import GrowthRecommendationService

router = APIRouter()


@router.get(
    "/{app_id}/growth/recommendations",
    response_model=GrowthRecommendationsOut,
)
async def get_growth_recommendations(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GrowthRecommendationsOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    items = await GrowthRecommendationService(session).recommendations_for_app(app.id)
    return GrowthRecommendationsOut(
        summary=GrowthRecommendationSummary(
            total=len(items),
            pricing=sum(1 for item in items if item.category == "pricing"),
        ),
        items=items,
    )
