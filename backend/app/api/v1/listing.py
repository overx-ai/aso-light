"""Listing optimizer routes (spec 019). Suggestion-only: nothing here writes to ASC."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.listing import (
    DEFAULT_LISTING_STOREFRONT,
    BrainstormIn,
    BrainstormOut,
    GapsOut,
    LocalizeIn,
    LocalizeOut,
    ProposalsOut,
    ProposeIn,
    RankingsRefreshOut,
)
from app.services.listing.errors import ListingError
from app.services.listing.service import ListingOptimizerService

router = APIRouter(tags=["listing"])

T = TypeVar("T")


async def listing_service(
    app_id: int, current_user: dict[str, Any], session: AsyncSession,
) -> ListingOptimizerService:
    app = await _get_verified_app(app_id, int(current_user["user_id"]), session)
    return ListingOptimizerService.for_app(session, app)


async def run_listing(call: Awaitable[T]) -> T:
    try:
        return await call
    except ListingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{app_id}/listing/brainstorm", response_model=BrainstormOut | None)
async def get_listing_brainstorm(
    app_id: int,
    storefront: str = Query(default=DEFAULT_LISTING_STOREFRONT, min_length=2, max_length=3),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BrainstormOut | None:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.stored_brainstorm(storefront))


@router.post("/{app_id}/listing/brainstorm", response_model=BrainstormOut)
async def run_listing_brainstorm(
    app_id: int,
    body: BrainstormIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BrainstormOut:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.brainstorm(body.storefront, body.competitor_track_id))


@router.get("/{app_id}/listing/gaps", response_model=GapsOut)
async def listing_gaps(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GapsOut:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.gaps())


@router.post("/{app_id}/listing/rankings/refresh", response_model=RankingsRefreshOut)
async def refresh_listing_rankings(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RankingsRefreshOut:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.refresh_rankings())


@router.post("/{app_id}/listing/proposals", response_model=ProposalsOut)
async def propose_listing(
    app_id: int,
    body: ProposeIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProposalsOut:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.propose(body.storefront))


@router.post("/{app_id}/listing/localize", response_model=LocalizeOut)
async def localize_listing(
    app_id: int,
    body: LocalizeIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LocalizeOut:
    service = await listing_service(app_id, current_user, session)
    return await run_listing(service.localize(body.proposal, body.storefront, body.locales))
