"""MCP tools for keyword analysis — iTunes search/suggestions, keyword
tracking, ranking history, and competitor coverage.

Thin wrappers over the REST endpoints in ``app/api/v1/keywords.py`` —
every endpoint becomes a tool, preserving the same auth + ownership
chain through :func:`resolve_app`.
"""

from __future__ import annotations

import logging

from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.mcp.context import get_user_id, resolve_app, session_scope
from app.mcp.server import mcp
from app.models.competitor import CompetitorApp
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.territory import Territory
from app.schemas.keyword import (
    CompetitorCreate,
    CompetitorKeywordResult,
    CompetitorResponse,
    CrossLocalizationEntry,
    KeywordCreate,
    KeywordPaidMetrics30d,
    KeywordRankingHistory,
    KeywordSuggestion,
    KeywordTrackingResponse,
    RankDataPoint,
    SearchResult,
)
from app.services.keywords.cross_localization import get_cross_localization_table
from app.services.keywords.itunes_search import ITunesSearchService
from app.services.keywords.suggestions import ITunesSuggestionsService
from app.services.keywords.tracker import KeywordRankingTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tracking_response(
    tracking: KeywordTracking,
) -> KeywordTrackingResponse:
    """Build a KeywordTrackingResponse, computing latest_rank + delta."""
    rankings = sorted(tracking.rankings, key=lambda r: r.recorded_at)

    latest_rank: int | None = None
    rank_change: int | None = None
    if rankings:
        latest_rank = rankings[-1].rank
        if len(rankings) >= 2:
            prev_rank = rankings[-2].rank
            if latest_rank is not None and prev_rank is not None:
                # Positive = improved (rank went down numerically).
                rank_change = prev_rank - latest_rank

    return KeywordTrackingResponse(
        id=tracking.id,
        keyword=tracking.keyword,
        app_id=tracking.app_id,
        latest_rank=latest_rank,
        rank_change=rank_change,
        added_at=tracking.created_at,
    )


async def _list_tracked_keywords(
    app_id: int, with_paid: bool = False,
) -> list[KeywordTrackingResponse]:
    async with session_scope() as session:
        await resolve_app(app_id, session)
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
        rows = [_build_tracking_response(t) for t in trackings]

        if with_paid:
            from app.services.asa.joins import paid_organic_join

            paid = await paid_organic_join(
                session=session, app_id=app_id, user_id=get_user_id(), days=30,
            )
            paid_by_term = {p["term"].lower(): p for p in paid}
            for row in rows:
                match = paid_by_term.get((row.keyword.text or "").lower())
                if match is None or match["paid_impressions_30d"] == 0:
                    continue
                row.paid_metrics_30d = KeywordPaidMetrics30d(
                    impressions=match["paid_impressions_30d"],
                    taps=match["paid_taps_30d"],
                    installs=match["paid_installs_30d"],
                    spend_amount=float(match["paid_spend_30d"]),
                    spend_currency=match["paid_spend_currency"],
                )
        return rows


async def _refresh_keyword_rankings(app_id: int) -> dict[str, int]:
    async with session_scope() as session:
        await resolve_app(app_id, session)
        tracker = KeywordRankingTracker(session)
        recorded = await tracker.refresh_rankings(app_id)
        return {"recorded": recorded}


# ---------------------------------------------------------------------------
# Suggestions / search / cross-localization (not app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords.suggestions")
async def keyword_suggestions(
    term: str,
    locale: str = "en_us",
) -> list[KeywordSuggestion]:
    """Get autocomplete suggestions from iTunes hints API."""
    if not term:
        raise ToolError("term must be non-empty")
    service = ITunesSuggestionsService()
    suggestions = await service.get_suggestions(term, locale)
    return [KeywordSuggestion(term=s) for s in suggestions]


@mcp.tool(name="keywords.search")
async def keyword_search(
    term: str,
    country: str = "us",
) -> list[SearchResult]:
    """Search iTunes for apps ranking against a keyword term."""
    if not term:
        raise ToolError("term must be non-empty")
    service = ITunesSearchService()
    results = await service.search_apps(term, country)
    return [SearchResult(**r) for r in results]


@mcp.tool(name="keywords.cross_localization")
async def keyword_cross_localization() -> list[CrossLocalizationEntry]:
    """Get the static (territory, locale, indexed) mapping table."""
    data = get_cross_localization_table()
    return [CrossLocalizationEntry(**entry) for entry in data]


# ---------------------------------------------------------------------------
# Tracked keywords (app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords.list_for_app")
async def list_tracked_keywords(
    app_id: int, with_paid: bool = False,
) -> list[KeywordTrackingResponse]:
    """List every tracked keyword for an app.

    When ``with_paid=True``, joins each row with the 30-day ASA paid
    metrics from :func:`paid_organic_join`. The ``paid_metrics_30d`` field
    is populated only for terms with a matching ASA keyword and non-zero
    impressions; otherwise it stays ``None``. The default (``False``) is
    backward-compatible — no shape change for existing callers.
    """
    return await _list_tracked_keywords(app_id=app_id, with_paid=with_paid)


@mcp.tool(name="keyword_intel.list_for_app")
async def list_keyword_intel_rows(app_id: int) -> list[KeywordTrackingResponse]:
    """List the cached keyword-intel rows for an app.

    This is a parity alias for the REST-backed tracked-keywords table:
    same ownership chain, same cached DB read, same response shape.
    """
    return await _list_tracked_keywords(app_id=app_id)


@mcp.tool(name="keywords.add")
async def add_tracked_keyword(
    app_id: int,
    text: str,
    locale: str = "en-US",
) -> KeywordTrackingResponse:
    """Start tracking a keyword for an app."""
    body = KeywordCreate(text=text, locale=locale)
    async with session_scope() as session:
        await resolve_app(app_id, session)

        # Find or create the keyword row (shared across apps).
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

        existing = await session.execute(
            select(KeywordTracking).where(
                KeywordTracking.app_id == app_id,
                KeywordTracking.keyword_id == keyword.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ToolError("Keyword is already being tracked for this app")

        tracking = KeywordTracking(app_id=app_id, keyword_id=keyword.id)
        session.add(tracking)
        await session.flush()

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


@mcp.tool(name="keywords.remove")
async def remove_tracked_keyword(app_id: int, tracking_id: int) -> dict[str, str]:
    """Stop tracking a keyword for an app."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        result = await session.execute(
            select(KeywordTracking).where(
                KeywordTracking.id == tracking_id,
                KeywordTracking.app_id == app_id,
            )
        )
        tracking = result.scalar_one_or_none()
        if tracking is None:
            raise ToolError("Keyword tracking not found")

        await session.delete(tracking)
        await session.flush()
        return {"detail": "Keyword tracking removed"}


@mcp.tool(name="keywords.refresh_rankings")
async def refresh_keyword_rankings(app_id: int) -> dict[str, int]:
    """Refresh rankings for every tracked keyword on the app.

    Returns ``{"recorded": <count>}``.
    """
    return await _refresh_keyword_rankings(app_id=app_id)


@mcp.tool(name="keyword_intel.refresh")
async def refresh_keyword_intel(app_id: int) -> dict[str, int]:
    """Refresh the cached keyword-intel rows for an app.

    This is a parity alias for the REST ``POST /apps/{app_id}/keywords/refresh``
    workflow, surfaced under the product-facing keyword-intel namespace.
    """
    return await _refresh_keyword_rankings(app_id=app_id)


@mcp.tool(name="keywords.get_rankings")
async def get_keyword_rankings(
    app_id: int,
    tracking_id: int,
) -> list[KeywordRankingHistory]:
    """Get ranking history for a tracked keyword, grouped by territory."""
    async with session_scope() as session:
        await resolve_app(app_id, session)

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
            raise ToolError("Keyword tracking not found")

        rankings_result = await session.execute(
            select(KeywordRanking, Territory.code)
            .join(Territory, Territory.id == KeywordRanking.territory_id)
            .where(KeywordRanking.tracking_id == tracking_id)
            .order_by(KeywordRanking.recorded_at)
        )
        rows = rankings_result.all()

        by_territory: dict[str, list[RankDataPoint]] = {}
        for ranking, territory_code in rows:
            by_territory.setdefault(territory_code, []).append(
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


# ---------------------------------------------------------------------------
# Competitors (app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords.list_competitors")
async def list_competitors(app_id: int) -> list[CompetitorResponse]:
    """List competitor apps registered for an app."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        result = await session.execute(
            select(CompetitorApp)
            .where(CompetitorApp.app_id == app_id)
            .order_by(CompetitorApp.created_at.desc())
        )
        competitors = result.scalars().all()
        return [CompetitorResponse.model_validate(c) for c in competitors]


@mcp.tool(name="keywords.add_competitor")
async def add_competitor(
    app_id: int,
    asc_app_id: str,
    name: str,
    bundle_id: str | None = None,
) -> CompetitorResponse:
    """Register a competitor app to track."""
    body = CompetitorCreate(asc_app_id=asc_app_id, name=name, bundle_id=bundle_id)
    async with session_scope() as session:
        await resolve_app(app_id, session)
        competitor = CompetitorApp(
            app_id=app_id,
            asc_app_id=body.asc_app_id,
            name=body.name,
            bundle_id=body.bundle_id,
        )
        session.add(competitor)
        await session.flush()
        await session.refresh(competitor)
        return CompetitorResponse.model_validate(competitor)


@mcp.tool(name="keywords.remove_competitor")
async def remove_competitor(app_id: int, competitor_id: int) -> dict[str, str]:
    """Unregister a competitor app."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        result = await session.execute(
            select(CompetitorApp).where(
                CompetitorApp.id == competitor_id,
                CompetitorApp.app_id == app_id,
            )
        )
        competitor = result.scalar_one_or_none()
        if competitor is None:
            raise ToolError("Competitor not found")

        await session.delete(competitor)
        await session.flush()
        return {"detail": "Competitor removed"}


async def _check_competitor_keywords(
    app_id: int, competitor_id: int,
) -> list[CompetitorKeywordResult]:
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        comp_result = await session.execute(
            select(CompetitorApp).where(
                CompetitorApp.id == competitor_id,
                CompetitorApp.app_id == app_id,
            )
        )
        competitor = comp_result.scalar_one_or_none()
        if competitor is None:
            raise ToolError("Competitor not found")

        trackings_result = await session.execute(
            select(KeywordTracking)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == app_id)
        )
        trackings = trackings_result.scalars().all()
        if not trackings:
            return []

        search_service = ITunesSearchService()
        results: list[CompetitorKeywordResult] = []
        for tracking in trackings:
            keyword_text = tracking.keyword.text
            search_results = await search_service.search_apps(
                keyword_text, country="us",
            )
            our_rank: int | None = None
            comp_rank: int | None = None
            for sr in search_results:
                if sr["app_id"] == app.asc_app_id:
                    our_rank = sr["position"]
                if sr["app_id"] == competitor.asc_app_id:
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


@mcp.tool(name="keywords.list_competitor_keywords")
async def list_competitor_keywords(
    app_id: int,
    competitor_id: int,
) -> list[CompetitorKeywordResult]:
    """For each tracked keyword of the app, report where the competitor and
    we currently rank in the iTunes US SERP.

    NOTE: this endpoint runs an iTunes search per tracked keyword, so it may
    take a few seconds for apps with many tracked keywords.
    """
    return await _check_competitor_keywords(app_id, competitor_id)


@mcp.tool(name="keywords.add_competitor_keywords")
async def add_competitor_keywords(
    app_id: int,
    competitor_id: int,
) -> list[CompetitorKeywordResult]:
    """Alias for ``keywords.list_competitor_keywords`` — kicks off the
    SERP-position matrix computation between the competitor and the app's
    tracked keywords. Naming preserved for spec parity with the REST POST.
    """
    return await _check_competitor_keywords(app_id, competitor_id)
