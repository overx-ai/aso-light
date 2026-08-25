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

from app.api.v1.keywords import (
    COMPETITOR_KEYWORD_CAP,
    _build_tracking_response,
    _compute_competitor_keyword_matrix,
)
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
from app.schemas.keyword_intel import KeywordIntelOut, KeywordIntelRefreshOut
from app.services.keyword_intel.service import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    MAX_DAYS,
    MAX_LIMIT,
    list_intel,
    run_providers,
)
from app.services.keywords.cross_localization import get_cross_localization_table
from app.services.keywords.itunes_search import (
    ITunesSearchService,
    is_valid_track_id,
)
from app.services.keywords.suggestions import ITunesSuggestionsService
from app.services.keywords.tracker import KeywordRankingTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Suggestions / search / cross-localization (not app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords_suggestions")
async def keyword_suggestions(
    term: str,
    country: str = "us",
    locale: str | None = None,
) -> list[KeywordSuggestion]:
    """Get autocomplete suggestions from iTunes hints API.

    ``country`` is a two-letter storefront code ("us", "de", …) and selects both
    the store and the language of the hints. ``locale`` is a deprecated alias
    ("en_us" → "us") that applies only while ``country`` is left at its "us"
    default — an explicit ``country`` always wins.
    """
    if not term:
        raise ToolError("term must be non-empty")
    service = ITunesSuggestionsService()
    suggestions = await service.get_suggestions(term, country, locale=locale)
    return [KeywordSuggestion(term=s) for s in suggestions]


@mcp.tool(name="keywords_search")
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


@mcp.tool(name="keywords_cross_localization")
async def keyword_cross_localization() -> list[CrossLocalizationEntry]:
    """Get the static (territory, locale, indexed) mapping table."""
    data = get_cross_localization_table()
    return [CrossLocalizationEntry(**entry) for entry in data]


# ---------------------------------------------------------------------------
# Tracked keywords (app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords_list_for_app")
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
        rows = [_build_tracking_response(t) for t in result.scalars().all()]

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


@mcp.tool(name="keywords_add")
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


@mcp.tool(name="keywords_remove")
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


@mcp.tool(name="keywords_refresh_rankings")
async def refresh_keyword_rankings(app_id: int) -> dict[str, int]:
    """Refresh rankings for every tracked keyword on the app.

    Returns ``{"recorded": <count>}``.
    """
    async with session_scope() as session:
        await resolve_app(app_id, session)
        tracker = KeywordRankingTracker(session)
        recorded = await tracker.refresh_rankings(app_id)
        return {"recorded": recorded}


@mcp.tool(name="keywords_get_rankings")
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
# Keyword intelligence — volume/difficulty cache (app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keyword_intel_list")
async def list_keyword_intel(
    app_id: int,
    keyword: list[str] | None = None,
    locale: str | None = None,
    source: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[KeywordIntelOut]:
    """Read the cached keyword-intel rows (volume + difficulty) for an app.

    Mirrors ``GET /apps/{app_id}/keyword-intel``: a pure cache read, newest
    first, optionally narrowed to specific ``keyword`` terms, a ``locale``, or
    a single ``source`` (``asa_search_terms``, ``asa_recommendations``, …).
    Call ``keyword_intel_refresh_providers`` first if the cache is cold.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}")
    async with session_scope() as session:
        await resolve_app(app_id, session)
        return await list_intel(
            session,
            app_id,
            keywords=keyword,
            locale=locale,
            source=source,
            limit=limit,
        )


@mcp.tool(name="keyword_intel_refresh_providers")
async def refresh_keyword_intel_providers(
    app_id: int,
    provider: str | None = None,
    days: int = DEFAULT_DAYS,
) -> KeywordIntelRefreshOut:
    """Run the keyword-intel providers and upsert their rows into the cache.

    Mirrors ``POST /apps/{app_id}/keyword-intel/refresh``. Runs every
    registered provider unless ``provider`` names one. A provider that fails is
    reported in ``skipped_sources`` while the rest still run. This is NOT
    ``keywords_refresh_rankings`` — that one re-scrapes iTunes SERP ranks for
    tracked keywords.
    """
    if not 1 <= days <= MAX_DAYS:
        raise ToolError(f"days must be between 1 and {MAX_DAYS}")
    async with session_scope() as session:
        await resolve_app(app_id, session)
        try:
            return await run_providers(
                session,
                app_id,
                days=days,
                provider=provider,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Competitors (app-scoped)
# ---------------------------------------------------------------------------


@mcp.tool(name="keywords_list_competitors")
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


@mcp.tool(name="keywords_add_competitor")
async def add_competitor(
    app_id: int,
    asc_app_id: str,
    name: str,
    bundle_id: str | None = None,
) -> CompetitorResponse:
    """Register a competitor app to track.

    ``asc_app_id`` must be the numeric iTunes track ID — a non-numeric value can
    never match in the competitor rank comparison and is rejected.
    """
    if not is_valid_track_id(asc_app_id):
        raise ToolError("asc_app_id must be the numeric iTunes track ID")
    body = CompetitorCreate(
        asc_app_id=asc_app_id.strip(), name=name, bundle_id=bundle_id,
    )
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


@mcp.tool(name="keywords_remove_competitor")
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

        # Cap the external fan-out (one iTunes search per tracked keyword).
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
            list(trackings), app.asc_app_id, competitor.asc_app_id,
        )


@mcp.tool(name="keywords_list_competitor_keywords")
async def list_competitor_keywords(
    app_id: int,
    competitor_id: int,
) -> list[CompetitorKeywordResult]:
    """For each tracked keyword of the app, report where the competitor and
    we currently rank in the iTunes US SERP.

    NOTE: this runs one iTunes search per tracked keyword (bounded concurrency),
    capped at the 50 most recently added keywords. Apps with more tracked
    keywords are truncated.
    """
    return await _check_competitor_keywords(app_id, competitor_id)


@mcp.tool(name="keywords_add_competitor_keywords")
async def add_competitor_keywords(
    app_id: int,
    competitor_id: int,
) -> list[CompetitorKeywordResult]:
    """Alias for ``keywords.list_competitor_keywords`` — kicks off the
    SERP-position matrix computation between the competitor and the app's
    tracked keywords. Naming preserved for spec parity with the REST POST.
    """
    return await _check_competitor_keywords(app_id, competitor_id)
