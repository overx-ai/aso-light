"""Read + refresh helpers shared by the REST router and the MCP tools.

Both surfaces (``app/api/v1/keyword_intel.py`` and the ``keyword_intel_*``
tools in ``app/mcp/tools/keywords.py``) call in here so they can never drift
on provider order, per-provider failure isolation, filtering, or logging.
Neither helper does ownership checks — the caller has already run
``_get_verified_app`` / ``resolve_app``.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword_intel import KeywordIntelCache
from app.schemas.keyword_intel import KeywordIntelOut, KeywordIntelRefreshOut
from app.services.keyword_intel.asa_recommendations import (
    ASARecommendationsProvider,
)
from app.services.keyword_intel.asa_search_terms import ASASearchTermsProvider
from app.services.keyword_intel.base import KeywordIntelProvider, upsert_intel

logger = logging.getLogger(__name__)

# Provider order is also the merge priority used by callers that read multiple
# rows for the same (keyword, locale): later sources override earlier on the
# same field. Today both free providers slot in; paid providers will be
# appended without changing this tuple's structure.
PROVIDER_FACTORIES: tuple[type[KeywordIntelProvider], ...] = (
    ASASearchTermsProvider,
    ASARecommendationsProvider,
)

# Request bounds, shared by both surfaces so the REST query validators and the
# MCP argument checks can never drift apart.
DEFAULT_DAYS = 30
MAX_DAYS = 365
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


def provider_names() -> list[str]:
    """Registered provider keys, in run order."""
    return [factory.name for factory in PROVIDER_FACTORIES]


async def run_providers(
    session: AsyncSession,
    app_id: int,
    *,
    days: int = DEFAULT_DAYS,
    provider: str | None = None,
) -> KeywordIntelRefreshOut:
    """Run every registered provider (or just ``provider``) and cache the rows.

    A failing provider is logged into ``skipped_sources`` and the rest still
    run — one broken upstream must not abort the whole refresh. Raises
    ``ValueError`` for an unknown ``provider`` name.
    """
    factories = PROVIDER_FACTORIES
    if provider is not None:
        factories = tuple(f for f in PROVIDER_FACTORIES if f.name == provider)
        if not factories:
            raise ValueError(
                f"Unknown provider {provider!r}; expected one of {provider_names()}"
            )

    by_source: dict[str, int] = {}
    skipped: dict[str, str] = {}
    total = 0
    for factory in factories:
        instance = factory()
        try:
            rows = await instance.fetch(app_id=app_id, session=session, days=days)
        except Exception as exc:  # noqa: BLE001 — log + continue per provider
            logger.warning(
                "Keyword-intel provider %s failed: %s",
                instance.name,
                exc,
            )
            skipped[instance.name] = str(exc)
            continue
        written = await upsert_intel(session, app_id, rows)
        by_source[instance.name] = written
        total += written

    logger.info(
        "Keyword-intel refresh app=%s wrote=%d by_source=%s skipped=%s",
        app_id,
        total,
        by_source,
        skipped,
    )
    return KeywordIntelRefreshOut(
        written_total=total,
        by_source=by_source,
        skipped_sources=skipped,
    )


async def list_intel(
    session: AsyncSession,
    app_id: int,
    *,
    keywords: list[str] | None = None,
    locale: str | None = None,
    source: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[KeywordIntelOut]:
    """Read cached intel rows for an app, newest first."""
    stmt = select(KeywordIntelCache).where(KeywordIntelCache.app_id == app_id)
    if keywords:
        stmt = stmt.where(KeywordIntelCache.keyword.in_(keywords))
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
