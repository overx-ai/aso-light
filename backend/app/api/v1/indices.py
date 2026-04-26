import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.economic_index import EconomicIndex
from app.services.indices.refresh import IndexRefreshService

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_INDEX_TYPES = {"ppp", "bigmac", "netflix", "spotify"}


@router.get("/status")
async def index_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return last refresh timestamps and record counts per index type."""
    result = await session.execute(
        select(
            EconomicIndex.index_type,
            func.count(EconomicIndex.id).label("count"),
            func.max(EconomicIndex.updated_at).label("last_updated"),
            func.max(EconomicIndex.reference_date).label("latest_reference_date"),
        ).group_by(EconomicIndex.index_type)
    )
    rows = result.all()

    statuses: dict[str, dict[str, Any]] = {}
    for row in rows:
        statuses[row.index_type] = {
            "count": row.count,
            "last_updated": row.last_updated.isoformat() if row.last_updated else None,
            "latest_reference_date": (
                row.latest_reference_date.isoformat()
                if row.latest_reference_date
                else None
            ),
        }

    # Include types that have no data yet
    for idx_type in VALID_INDEX_TYPES:
        if idx_type not in statuses:
            statuses[idx_type] = {
                "count": 0,
                "last_updated": None,
                "latest_reference_date": None,
            }

    return {"indices": statuses}


@router.post("/refresh")
async def refresh_indices(
    index_type: str | None = Query(
        default=None,
        description="Specific index type to refresh. If omitted, all types are refreshed.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Trigger refresh of economic indices.

    Optionally specify an index_type query parameter to refresh only
    one type (ppp, bigmac, netflix, spotify).
    """
    service = IndexRefreshService(session)

    if index_type is not None:
        if index_type not in VALID_INDEX_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid index type: {index_type}. "
                       f"Valid types: {', '.join(sorted(VALID_INDEX_TYPES))}",
            )
        count = await service.refresh_type(index_type)
        return {"refreshed": {index_type: count}}

    results = await service.refresh_all()
    return {"refreshed": results}
