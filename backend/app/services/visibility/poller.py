"""Poll iTunes search and persist a top-N snapshot for a watched keyword."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visibility import (
    KeywordVisibilityResult,
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.services.keywords.itunes_search import ITunesSearchService

logger = logging.getLogger(__name__)

MAX_RESULTS_PER_SNAPSHOT = 20


async def poll_watch(
    watch: KeywordVisibilityWatch,
    session: AsyncSession,
    *,
    search_service: ITunesSearchService | None = None,
) -> KeywordVisibilitySnapshot:
    """Run one poll for a watched (keyword, country) pair.

    Calls iTunes Search, captures the top ``MAX_RESULTS_PER_SNAPSHOT`` rows,
    and creates a snapshot + result rows. Caller is responsible for
    committing the transaction.
    """
    svc = search_service or ITunesSearchService()
    results = await svc.search_apps(
        watch.text, country=watch.country.lower(), limit=MAX_RESULTS_PER_SNAPSHOT,
    )
    snapshot = KeywordVisibilitySnapshot(
        watch_id=watch.id,
        results_count=len(results),
    )
    session.add(snapshot)
    await session.flush()

    for r in results[:MAX_RESULTS_PER_SNAPSHOT]:
        session.add(
            KeywordVisibilityResult(
                snapshot_id=snapshot.id,
                position=int(r.get("position") or 0),
                track_id=str(r.get("app_id") or ""),
                name=str(r.get("name") or ""),
                bundle_id=str(r.get("bundle_id") or ""),
                icon_url=str(r.get("icon_url") or ""),
            )
        )

    watch.last_polled_at = datetime.now(timezone.utc)
    await session.flush()

    logger.info(
        "Visibility poll: app=%s watch=%s text=%r country=%s results=%d",
        watch.app_id, watch.id, watch.text, watch.country, len(results),
    )
    return snapshot
