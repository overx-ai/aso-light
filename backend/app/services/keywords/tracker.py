"""Keyword ranking tracker — refreshes rankings for tracked keywords."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.app import App
from app.models.keyword import KeywordRanking, KeywordTracking
from app.models.territory import Territory
from app.services.keywords.concurrency import gather_bounded
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)

DEFAULT_TERRITORY_CODES = ["US"]

# Hard cap on (keyword x territory) external calls per refresh, and the max
# in-flight iTunes requests. Bounds the work done in-request while holding the
# DB session. NOTE: a future background-task model (enqueue + poll) would be a
# better home for this fan-out than an in-request loop.
MAX_RANKING_CHECKS = 250
_REFRESH_CONCURRENCY = 5


class KeywordRankingTracker:
    """Tracks keyword rankings for apps over time."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.search_service = ITunesSearchService()

    async def refresh_rankings(
        self,
        app_id: int,
        territory_codes: list[str] | None = None,
    ) -> int:
        """Refresh rankings for all tracked keywords of an app.

        Args:
            app_id: Database app ID.
            territory_codes: Territory codes to check (defaults to US only).

        Returns:
            Number of rankings recorded.
        """
        codes = territory_codes or DEFAULT_TERRITORY_CODES

        # Load the app to get its ASC app ID
        app_result = await self.session.execute(
            select(App).where(App.id == app_id)
        )
        app = app_result.scalar_one_or_none()
        if app is None:
            logger.warning("App id=%d not found for ranking refresh", app_id)
            return 0

        # Load all keyword trackings with their keywords
        tracking_result = await self.session.execute(
            select(KeywordTracking)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == app_id)
        )
        trackings = tracking_result.scalars().all()

        if not trackings:
            return 0

        # Resolve territory codes to IDs
        territory_result = await self.session.execute(
            select(Territory).where(Territory.code.in_(codes))
        )
        territories = territory_result.scalars().all()
        territory_id_map = {t.code: t.id for t in territories}

        # Build the bounded work list: (tracking_id, keyword_text, territory_id,
        # country). Skip unknown territory codes once, up front. Cap the total
        # number of external checks so one refresh can't fan out unboundedly
        # while holding the DB session.
        jobs: list[tuple[int, str, int, str]] = []
        for tracking in trackings:
            keyword_text = tracking.keyword.text
            for code in codes:
                territory_id = territory_id_map.get(code)
                if territory_id is None:
                    logger.warning(
                        "Territory code=%r not found in database, skipping",
                        code,
                    )
                    continue
                jobs.append(
                    (tracking.id, keyword_text, territory_id, code.lower())
                )

        if len(jobs) > MAX_RANKING_CHECKS:
            logger.warning(
                "Ranking refresh for app_id=%d truncated from %d to %d checks",
                app_id, len(jobs), MAX_RANKING_CHECKS,
            )
            jobs = jobs[:MAX_RANKING_CHECKS]

        if not jobs:
            return 0

        now = datetime.now(timezone.utc)

        async with httpx.AsyncClient(timeout=15.0) as client:
            async def _rank(job: tuple[int, str, int, str]) -> int | None:
                _, keyword_text, _, country = job
                return await self.search_service.get_app_rank(
                    term=keyword_text,
                    app_id=app.asc_app_id,
                    country=country,
                    client=client,
                )

            ranks = await gather_bounded(
                jobs, _rank, concurrency=_REFRESH_CONCURRENCY,
            )

        recorded = 0
        for (tracking_id, _, territory_id, _), rank in zip(
            jobs, ranks, strict=True,
        ):
            self.session.add(
                KeywordRanking(
                    tracking_id=tracking_id,
                    territory_id=territory_id,
                    rank=rank,
                    recorded_at=now,
                )
            )
            recorded += 1

        await self.session.flush()

        logger.info(
            "Recorded %d rankings for app_id=%d across %d territories",
            recorded,
            app_id,
            len(codes),
        )
        return recorded
