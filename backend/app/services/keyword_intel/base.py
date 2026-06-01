"""Provider ABC + the cache-write helper every provider funnels through.

Adding a new source is two steps:
1. Subclass :class:`KeywordIntelProvider` and implement ``fetch()``.
2. Call :func:`upsert_intel` with the rows it returns. The ABC does not own
   I/O, so providers can be unit-tested without a session.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword_intel import KeywordIntelCache


@dataclass(slots=True)
class KeywordIntel:
    """One project-normalized intel signal for a keyword in a locale.

    All scores are 0–100 ints (or ``None`` if the source can't produce them).
    The provider's native value lives in ``raw_score`` for debugging — it is
    not safe to compare across sources directly.
    """

    keyword: str
    locale: str
    source: str
    volume_score: int | None = None
    difficulty_score: int | None = None
    raw_score: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class KeywordIntelProvider(ABC):
    """Abstract source of :class:`KeywordIntel` rows.

    Providers should be **pure** with respect to the DB session — they call
    out to whatever upstream they wrap (ASA API, MobileAction, etc.) and
    return rows. Persistence is the caller's responsibility (via
    :func:`upsert_intel`) so the ABC stays test-friendly.
    """

    #: Stable identifier written to ``KeywordIntelCache.source``. Must be
    #: unique across providers and stable across versions.
    name: str

    @abstractmethod
    async def fetch(
        self, *, app_id: int, session: AsyncSession, **kwargs: Any,
    ) -> list[KeywordIntel]:
        """Pull intel rows. Implementations may take provider-specific kwargs."""


async def upsert_intel(
    session: AsyncSession, app_id: int, rows: list[KeywordIntel],
) -> int:
    """Insert/update one ``keyword_intel_cache`` row per intel.

    Per-row delete-then-insert (rather than a dialect-specific ON CONFLICT)
    keeps the helper portable between SQLite-dev and Postgres-prod. Volume
    per call is bounded by the provider's batch size, so the cost is fine.
    Returns the number of rows written.
    """
    if not rows:
        return 0
    written = 0
    for r in rows:
        existing = await session.execute(
            select(KeywordIntelCache).where(
                KeywordIntelCache.app_id == app_id,
                KeywordIntelCache.keyword == r.keyword,
                KeywordIntelCache.locale == r.locale,
                KeywordIntelCache.source == r.source,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(
                KeywordIntelCache(
                    app_id=app_id,
                    keyword=r.keyword,
                    locale=r.locale,
                    source=r.source,
                    volume_score=r.volume_score,
                    difficulty_score=r.difficulty_score,
                    raw_score=r.raw_score,
                    extra=r.extra or None,
                )
            )
        else:
            row.volume_score = r.volume_score
            row.difficulty_score = r.difficulty_score
            row.raw_score = r.raw_score
            row.extra = r.extra or None
        written += 1
    await session.flush()
    return written
