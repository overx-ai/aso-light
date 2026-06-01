"""REST endpoints for the keyword-intelligence subsystem.

Two routes today:

* ``POST /apps/{app_id}/keyword-intel/refresh`` — runs the configured providers
  (currently ``asa_search_terms`` + ``asa_recommendations``) and writes results
  to the cache. Idempotent.
* ``GET /apps/{app_id}/keyword-intel`` — reads the cache, optionally filtered
  by keyword(s), locale, or source.

The frontend wiring + MCP mirror land in the next iteration.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.keyword_intel import KeywordIntelCache
from app.schemas.keyword_intel import (
    KeywordIntelOut,
    KeywordIntelRefreshOut,
)
from app.services.keyword_intel import upsert_intel
from app.services.keyword_intel.asa_recommendations import (
    ASARecommendationsProvider,
)
from app.services.keyword_intel.asa_search_terms import ASASearchTermsProvider

logger = logging.getLogger(__name__)
router = APIRouter()


# Provider order is also the merge priority used by callers that read multiple
# rows for the same (keyword, locale): later sources override earlier on the
# same field. Today both free providers slot in; paid providers will be
# appended without changing this list's structure.
_PROVIDERS_FACTORY = (
    ASASearchTermsProvider,
    ASARecommendationsProvider,
)


@router.post(
    "/{app_id}/keyword-intel/refresh",
    response_model=KeywordIntelRefreshOut,
    status_code=status.HTTP_200_OK,
)
async def refresh_keyword_intel(
    app_id: int,
    days: int = Query(default=30, ge=1, le=365),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KeywordIntelRefreshOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    by_source: dict[str, int] = {}
    skipped: dict[str, str] = {}
    total = 0
    for factory in _PROVIDERS_FACTORY:
        provider = factory()
        try:
            rows = await provider.fetch(
                app_id=app_id, session=session, days=days,
            )
        except Exception as exc:  # noqa: BLE001 — log + continue per provider
            logger.warning(
                "Keyword-intel provider %s failed: %s", provider.name, exc,
            )
            skipped[provider.name] = str(exc)
            continue
        written = await upsert_intel(session, app_id, rows)
        by_source[provider.name] = written
        total += written

    logger.info(
        "Keyword-intel refresh app=%s wrote=%d by_source=%s skipped=%s",
        app_id, total, by_source, skipped,
    )
    return KeywordIntelRefreshOut(
        written_total=total,
        by_source=by_source,
        skipped_sources=skipped,
    )


@router.get(
    "/{app_id}/keyword-intel",
    response_model=list[KeywordIntelOut],
)
async def list_keyword_intel(
    app_id: int,
    keyword: list[str] | None = Query(
        default=None,
        description=(
            "Repeat to look up multiple keywords in one call: "
            "?keyword=foo&keyword=bar"
        ),
    ),
    locale: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[KeywordIntelOut]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    stmt = select(KeywordIntelCache).where(KeywordIntelCache.app_id == app_id)
    if keyword:
        stmt = stmt.where(KeywordIntelCache.keyword.in_(keyword))
    if locale:
        stmt = stmt.where(KeywordIntelCache.locale == locale)
    if source:
        stmt = stmt.where(KeywordIntelCache.source == source)
    # Newest first so the UI naturally shows the freshest signal.
    stmt = stmt.order_by(KeywordIntelCache.fetched_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        KeywordIntelOut(
            keyword=r.keyword,
            locale=r.locale,
            source=r.source,
            volume_score=r.volume_score,
            difficulty_score=r.difficulty_score,
            raw_score=r.raw_score,
            extra=r.extra,
            fetched_at=r.fetched_at,
        )
        for r in rows
    ]
