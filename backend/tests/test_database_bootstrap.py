from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.territory import Territory


def test_bootstrap_database_runs_migrations_and_seeds_territories():
    from app.db.bootstrap import bootstrap_database

    async def go() -> tuple[set[str], int]:
        db_path = Path(tempfile.gettempdir()) / f"aso-light-bootstrap-{uuid.uuid4().hex}.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
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
    assert territory_count == 202
