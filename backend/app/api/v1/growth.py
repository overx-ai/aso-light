from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.growth import GrowthRecommendationOut, GrowthRecommendationsOut
from app.services.growth.recommendations import generate_growth_recommendations

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
    await _get_verified_app(app_id, user_id, session)
    recommendations = await generate_growth_recommendations(
        session=session,
        app_id=app_id,
    )
    return GrowthRecommendationsOut(
        items=[
            GrowthRecommendationOut.model_validate(asdict(rec))
            for rec in recommendations
        ]
    )
