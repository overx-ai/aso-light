from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.data.seed import seed_territories
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


def _alembic_config(database_url: str) -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _upgrade_head(database_url: str) -> None:
    command.upgrade(_alembic_config(database_url), "head")


async def run_migrations(database_url: str | None = None) -> None:
    target_database_url = database_url or settings.DATABASE_URL
    logger.info("Running Alembic migrations to head")
    await asyncio.to_thread(_upgrade_head, target_database_url)


async def bootstrap_database(
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if (database_url is None) != (session_factory is None):
        raise ValueError(
            "bootstrap_database overrides require both database_url and session_factory",
        )

    target_database_url = database_url or settings.DATABASE_URL
    await run_migrations(database_url=target_database_url)

    if session_factory is None:
        target_session_factory = async_session_factory
    else:
        target_session_factory = session_factory

    async with target_session_factory() as session:
        await seed_territories(session)
