"""Keyword ranking tracker — refreshes rankings for tracked keywords."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.app import App
from app.models.keyword import KeywordRanking, KeywordTracking
from app.models.territory import Territory
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)

DEFAULT_TERRITORY_CODES = ["US"]


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

        recorded = 0
        now = datetime.now(timezone.utc)

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

                rank = await self.search_service.get_app_rank(
                    term=keyword_text,
                    app_id=app.asc_app_id,
                    country=code.lower(),
                )

                ranking = KeywordRanking(
                    tracking_id=tracking.id,
                    territory_id=territory_id,
                    rank=rank,
                    recorded_at=now,
                )
                self.session.add(ranking)
                recorded += 1

        await self.session.flush()

        logger.info(
            "Recorded %d rankings for app_id=%d across %d territories",
            recorded,
            app_id,
            len(codes),
        )
        return recorded
