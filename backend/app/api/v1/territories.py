"""Territory API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.territory import Territory
from app.schemas.territory import TerritoryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[TerritoryResponse])
async def list_territories(
    session: AsyncSession = Depends(get_session),
) -> list[TerritoryResponse]:
    """Return all territories with currency and VAT rate info."""
    result = await session.execute(
        select(Territory).order_by(Territory.code)
    )
    territories = result.scalars().all()
    return [TerritoryResponse.model_validate(t) for t in territories]
