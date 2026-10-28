"""Brainstorm for newly added apps after an apps sync, off the request path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import select

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.app import App
from app.models.keyword_intel import KeywordIntelCache
from app.services.listing.service import BRAINSTORM_SOURCE, ListingOptimizerService
from app.services.metadata.translate import build_translator

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


async def brainstorm_new_apps(app_ids: Iterable[int]) -> None:
    """Sequential and failure-isolated: a sync must never fail or slow down because of this."""
    for app_id in app_ids:
        try:
            async with async_session_factory() as session:
                already = (await session.execute(
                    select(KeywordIntelCache.id).where(
                        KeywordIntelCache.app_id == app_id,
                        KeywordIntelCache.source == BRAINSTORM_SOURCE,
                    ).limit(1)
                )).scalar_one_or_none()
                app = await session.get(App, app_id)
                if already is not None or app is None:
                    continue
                service = ListingOptimizerService(
                    session, app, translator=build_translator(settings),
                )
                await service.brainstorm()
                await session.commit()
                logger.info("Post-sync brainstorm done app_id=%s", app_id)
        except Exception:  # noqa: BLE001 — background job, logged and dropped
            logger.exception("Post-sync brainstorm failed app_id=%s", app_id)


def schedule_brainstorm(app_ids: Iterable[int]) -> None:
    ids = list(app_ids)
    if not ids:
        return
    task = asyncio.create_task(brainstorm_new_apps(ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
