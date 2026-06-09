from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.territories import TERRITORIES
from app.models.territory import Territory


def _temp_database_url(prefix: str) -> tuple[Path, str]:
    db_path = Path(tempfile.gettempdir()) / f"{prefix}-{uuid.uuid4().hex}.db"
    return db_path, f"sqlite+aiosqlite:///{db_path}"


def test_bootstrap_database_runs_migrations_and_seeds_territories():
    from app.db.bootstrap import bootstrap_database

    async def go() -> tuple[set[str], int]:
        db_path, database_url = _temp_database_url("aso-light-bootstrap")
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            await bootstrap_database(
                database_url=database_url,
                session_factory=session_factory,
            )

            async with engine.connect() as conn:
                table_names = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names()),
                )

            async with session_factory() as session:
                territory_count = await session.scalar(
                    select(func.count()).select_from(Territory),
                )

            return table_names, territory_count or 0
        finally:
            await engine.dispose()
            if db_path.exists():
                db_path.unlink()

    table_names, territory_count = asyncio.run(go())

    assert "alembic_version" in table_names
    assert "territories" in table_names
    assert territory_count == len(TERRITORIES)


def test_run_migrations_adds_personal_access_tokens_for_legacy_head():
    from app.db.bootstrap import _alembic_config, run_migrations

    async def go() -> tuple[set[str], set[str]]:
        db_path, database_url = _temp_database_url("aso-light-pat-migration")
        engine = create_async_engine(database_url)

        try:
            await asyncio.to_thread(
                command.upgrade,
                _alembic_config(database_url),
                "858cfcb132f1",
            )

            async with engine.connect() as conn:
                legacy_tables = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names()),
                )

            await run_migrations(database_url=database_url)

            async with engine.connect() as conn:
                head_tables = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names()),
                )

            return legacy_tables, head_tables
        finally:
            await engine.dispose()
            if db_path.exists():
                db_path.unlink()

    legacy_tables, head_tables = asyncio.run(go())

    assert "personal_access_tokens" not in legacy_tables
    assert "personal_access_tokens" in head_tables


def test_bootstrap_database_rejects_database_url_override_without_session_factory():
    from app.db.bootstrap import bootstrap_database

    async def go() -> None:
        db_path, database_url = _temp_database_url("aso-light-bootstrap-target")

        try:
            with pytest.raises(
                ValueError,
                match="database_url and session_factory",
            ):
                await bootstrap_database(database_url=database_url)
        finally:
            if db_path.exists():
                db_path.unlink()

    asyncio.run(go())


def test_bootstrap_database_rejects_session_factory_override_without_database_url():
    from app.db.bootstrap import bootstrap_database

    async def go() -> None:
        db_path, database_url = _temp_database_url("aso-light-bootstrap-target")
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            with pytest.raises(
                ValueError,
                match="database_url and session_factory",
            ):
                await bootstrap_database(session_factory=session_factory)
        finally:
            await engine.dispose()
            if db_path.exists():
                db_path.unlink()

    asyncio.run(go())
