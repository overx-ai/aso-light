"""Keyword analysis API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_verified_app
from app.core.ratelimit import rate_limit
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.competitor import CompetitorApp
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.territory import Territory
from app.schemas.keyword import (
    CompetitorCreate,
    CompetitorKeywordResult,
    CompetitorResponse,
    CrossLocalizationEntry,
    KeywordCreate,
    KeywordRankingHistory,
    KeywordSuggestion,
    KeywordTrackingResponse,
    RankDataPoint,
    SearchResult,
)
from app.services.keywords.cross_localization import get_cross_localization_table
from app.services.keywords.itunes_search import (
    ITunesSearchService,
    is_valid_track_id,
)
from app.services.keywords.suggestions import ITunesSuggestionsService
from app.services.keywords.tracker import KeywordRankingTracker

# Max tracked keywords scanned in one competitor SERP comparison. Each keyword
# is a separate iTunes round-trip, so we cap the fan-out (bounded-concurrency
# batch) and surface truncation in the response/docstring.
COMPETITOR_KEYWORD_CAP = 50

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_tracking_response(
    tracking: KeywordTracking,
) -> KeywordTrackingResponse:
    """Build a KeywordTrackingResponse from a tracking ORM object.

    Computes latest_rank and rank_change from the loaded rankings.
    """
    rankings = sorted(tracking.rankings, key=lambda r: r.recorded_at)

    latest_rank: int | None = None
    rank_change: int | None = None

    if rankings:
        latest_rank = rankings[-1].rank
        if len(rankings) >= 2:
            prev_rank = rankings[-2].rank
            if latest_rank is not None and prev_rank is not None:
                # Positive = improved (rank went down numerically)
                rank_change = prev_rank - latest_rank

    return KeywordTrackingResponse(
        id=tracking.id,
        keyword=tracking.keyword,
        app_id=tracking.app_id,
        latest_rank=latest_rank,
        rank_change=rank_change,
        added_at=tracking.created_at,
    )


# ------------------------------------------------------------------
# Keyword Suggestions (not app-scoped)
# ------------------------------------------------------------------


@router.get("/keywords/suggestions", response_model=list[KeywordSuggestion])
async def get_suggestions(
    term: str = Query(..., min_length=1),
    country: str = Query(default="us"),
    locale: str | None = Query(
        default=None,
        deprecated=True,
        description=(
            "Deprecated alias for `country` (en_us → us). Ignored whenever "
            "`country` is set to anything but its `us` default."
        ),
    ),
    _current_user: dict[str, Any] = Depends(
        rate_limit("keywords.suggestions", per_min=30),
    ),
) -> list[KeywordSuggestion]:
    """Get autocomplete suggestions from iTunes hints API.

    ``country`` selects the storefront (`us`, `de`, …) and wins over the
    deprecated ``locale`` alias when both are sent and disagree; ``locale``
    applies only while ``country`` is left at its default. Rate-limited to 30
    requests/minute per user: each call fetches Apple from the shared backend IP.
    """
    service = ITunesSuggestionsService()
    suggestions = await service.get_suggestions(term, country, locale=locale)
    return [KeywordSuggestion(term=s) for s in suggestions]


@router.post("/keywords/search", response_model=list[SearchResult])
async def search_keywords(
    term: str = Query(..., min_length=1),
    country: str = Query(default="us"),
    _current_user: dict[str, Any] = Depends(
        rate_limit("keywords.search", per_min=30),
    ),
) -> list[SearchResult]:
    """Search iTunes for apps matching a keyword term.

    Rate-limited to 30 requests/minute per user: each call fetches Apple from
    the shared backend IP.
    """
    service = ITunesSearchService()
    results = await service.search_apps(term, country)
    return [SearchResult(**r) for r in results]


@router.get("/keywords/cross-localization", response_model=list[CrossLocalizationEntry])
async def get_cross_localization() -> list[CrossLocalizationEntry]:
    """Get the cross-localization mapping table."""
    data = get_cross_localization_table()
    return [CrossLocalizationEntry(**entry) for entry in data]


# ------------------------------------------------------------------
# Tracked Keywords (app-scoped)
# ------------------------------------------------------------------


@router.get(
    "/apps/{app_id}/keywords",
    response_model=list[KeywordTrackingResponse],
)
async def list_tracked_keywords(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[KeywordTrackingResponse]:
    """List all tracked keywords for an app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    result = await session.execute(
        select(KeywordTracking)
        .options(
            selectinload(KeywordTracking.keyword),
            selectinload(KeywordTracking.rankings),
        )
        .where(KeywordTracking.app_id == app_id)
        .order_by(KeywordTracking.created_at.desc())
    )
    trackings = result.scalars().all()

    return [_build_tracking_response(t) for t in trackings]


@router.post(
    "/apps/{app_id}/keywords",
    response_model=KeywordTrackingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_tracked_keyword(
    app_id: int,
    body: KeywordCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KeywordTrackingResponse:
    """Add a keyword to track for an app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    # Find or create the keyword
    keyword_result = await session.execute(
        select(Keyword).where(
            Keyword.text == body.text,
            Keyword.locale == body.locale,
        )
    )
    keyword = keyword_result.scalar_one_or_none()

    if keyword is None:
        keyword = Keyword(text=body.text, locale=body.locale)
        session.add(keyword)
        await session.flush()

    # Check if already tracked
    existing = await session.execute(
        select(KeywordTracking).where(
            KeywordTracking.app_id == app_id,
            KeywordTracking.keyword_id == keyword.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Keyword is already being tracked for this app",
        )

    tracking = KeywordTracking(app_id=app_id, keyword_id=keyword.id)
    session.add(tracking)
    await session.flush()

    # Reload with relationships
    reload_result = await session.execute(
        select(KeywordTracking)
        .options(
            selectinload(KeywordTracking.keyword),
            selectinload(KeywordTracking.rankings),
        )
        .where(KeywordTracking.id == tracking.id)
    )
    tracking = reload_result.scalar_one()

    return _build_tracking_response(tracking)


@router.delete(
    "/apps/{app_id}/keywords/{tracking_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_tracked_keyword(
    app_id: int,
    tracking_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Stop tracking a keyword for an app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    result = await session.execute(
        select(KeywordTracking).where(
            KeywordTracking.id == tracking_id,
            KeywordTracking.app_id == app_id,
        )
    )
    tracking = result.scalar_one_or_none()
    if tracking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword tracking not found",
        )

    await session.delete(tracking)
    await session.flush()


@router.get(
    "/apps/{app_id}/keywords/{tracking_id}/rankings",
    response_model=list[KeywordRankingHistory],
)
async def get_keyword_rankings(
    app_id: int,
    tracking_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[KeywordRankingHistory]:
    """Get ranking history for a tracked keyword."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    tracking_result = await session.execute(
        select(KeywordTracking)
        .options(selectinload(KeywordTracking.keyword))
        .where(
            KeywordTracking.id == tracking_id,
            KeywordTracking.app_id == app_id,
        )
    )
    tracking = tracking_result.scalar_one_or_none()
    if tracking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword tracking not found",
        )

    # Load rankings with territory info
    rankings_result = await session.execute(
        select(KeywordRanking, Territory.code)
        .join(Territory, Territory.id == KeywordRanking.territory_id)
        .where(KeywordRanking.tracking_id == tracking_id)
        .order_by(KeywordRanking.recorded_at)
    )
    rows = rankings_result.all()

    # Group by territory
    by_territory: dict[str, list[RankDataPoint]] = {}
    for ranking, territory_code in rows:
        if territory_code not in by_territory:
            by_territory[territory_code] = []
        by_territory[territory_code].append(
            RankDataPoint(
                date=ranking.recorded_at,
                rank=ranking.rank,
                territory_code=territory_code,
            )
        )

    return [
        KeywordRankingHistory(
            keyword_text=tracking.keyword.text,
            territory_code=tc,
            data_points=points,
        )
        for tc, points in by_territory.items()
    ]


@router.post(
    "/apps/{app_id}/keywords/refresh",
    response_model=dict[str, int],
)
async def refresh_keyword_rankings(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Refresh rankings for all tracked keywords of an app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    tracker = KeywordRankingTracker(session)
    recorded = await tracker.refresh_rankings(app_id)

    return {"recorded": recorded}


# ------------------------------------------------------------------
# Competitor endpoints (app-scoped)
# ------------------------------------------------------------------


@router.get(
    "/apps/{app_id}/competitors",
    response_model=list[CompetitorResponse],
)
async def list_competitors(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CompetitorResponse]:
    """List competitor apps for an app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    result = await session.execute(
        select(CompetitorApp)
        .where(CompetitorApp.app_id == app_id)
        .order_by(CompetitorApp.created_at.desc())
    )
    competitors = result.scalars().all()
    return [CompetitorResponse.model_validate(c) for c in competitors]


@router.post(
    "/apps/{app_id}/competitors",
    response_model=CompetitorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_competitor(
    app_id: int,
    body: CompetitorCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CompetitorResponse:
    """Add a competitor app to track.

    ``asc_app_id`` must be the numeric iTunes track ID — a non-numeric value can
    never match in the competitor rank comparison and is rejected with 400.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    if not is_valid_track_id(body.asc_app_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="asc_app_id must be the numeric iTunes track ID",
        )

    competitor = CompetitorApp(
        app_id=app_id,
        asc_app_id=body.asc_app_id.strip(),
        name=body.name,
        bundle_id=body.bundle_id,
    )
    session.add(competitor)
    await session.flush()
    await session.refresh(competitor)

    return CompetitorResponse.model_validate(competitor)


@router.delete(
    "/apps/{app_id}/competitors/{competitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_competitor(
    app_id: int,
    competitor_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a competitor app."""
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    result = await session.execute(
        select(CompetitorApp).where(
            CompetitorApp.id == competitor_id,
            CompetitorApp.app_id == app_id,
        )
    )
    competitor = result.scalar_one_or_none()
    if competitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Competitor not found",
        )

    await session.delete(competitor)
    await session.flush()


@router.post(
    "/apps/{app_id}/competitors/{competitor_id}/keywords",
    response_model=list[CompetitorKeywordResult],
)
async def check_competitor_keywords(
    app_id: int,
    competitor_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CompetitorKeywordResult]:
    """Check where a competitor ranks for our tracked keywords.

    Each tracked keyword is a separate iTunes search, so the fan-out is capped
    at ``COMPETITOR_KEYWORD_CAP`` (50) keywords and run with bounded concurrency
    over a single shared HTTP client. Apps with more tracked keywords are
    truncated (the most recently added ``COMPETITOR_KEYWORD_CAP`` are checked).
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    # Verify competitor belongs to this app
    comp_result = await session.execute(
        select(CompetitorApp).where(
            CompetitorApp.id == competitor_id,
            CompetitorApp.app_id == app_id,
        )
    )
    competitor = comp_result.scalar_one_or_none()
    if competitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Competitor not found",
        )

    # Get tracked keywords for this app, capped to bound the external fan-out.
    trackings_result = await session.execute(
        select(KeywordTracking)
        .options(selectinload(KeywordTracking.keyword))
        .where(KeywordTracking.app_id == app_id)
        .order_by(KeywordTracking.created_at.desc())
        .limit(COMPETITOR_KEYWORD_CAP)
    )
    trackings = trackings_result.scalars().all()

    if not trackings:
        return []

    return await _compute_competitor_keyword_matrix(
        trackings, app.asc_app_id, competitor.asc_app_id,
    )


async def _compute_competitor_keyword_matrix(
    trackings: list[KeywordTracking],
    our_app_id: str,
    competitor_app_id: str,
) -> list[CompetitorKeywordResult]:
    """Run the SERP comparison for each tracked keyword with bounded concurrency
    over a single shared iTunes client. Shared by the REST endpoint and the MCP
    tool so both honour the same cap + concurrency."""
    search_service = ITunesSearchService()
    keyword_texts = [t.keyword.text for t in trackings]
    batch = await search_service.search_apps_batch(
        [(text, "us") for text in keyword_texts],
    )

    results: list[CompetitorKeywordResult] = []
    for keyword_text, search_results in zip(keyword_texts, batch, strict=True):
        our_rank: int | None = None
        comp_rank: int | None = None
        for sr in search_results:
            if sr["app_id"] == our_app_id:
                our_rank = sr["position"]
            if sr["app_id"] == competitor_app_id:
                comp_rank = sr["position"]
        results.append(
            CompetitorKeywordResult(
                keyword_text=keyword_text,
                competitor_rank=comp_rank,
                our_rank=our_rank,
                territory_code="US",
            )
        )
    return results
