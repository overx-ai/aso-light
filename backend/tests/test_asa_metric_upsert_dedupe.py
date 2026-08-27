"""Re-sync must not duplicate metric rows (NULL-storefront grain).

``ASAMetricDaily`` is deduped by the unique grain
``(dim_kind, dim_id, date, storefront)`` and ``_upsert_metrics`` uses that
tuple as its ON CONFLICT arbiter. But ``storefront`` is nullable, and in
standard SQL (SQLite and PostgreSQL both) NULL is never equal to NULL inside a
unique index -- so two NULL-storefront rows at the same grain do NOT conflict.
The upsert silently degrades to a plain INSERT and every re-sync appends a
duplicate.

That is not an edge case: ``reports._selector`` sends no ``groupBy``, so Apple
never returns ``countryOrRegion`` and ``_metric_row`` stores
``storefront = None`` for EVERY row. So this hits 100% of metric rows, and the
analytics rollups (which ``func.sum`` over them) multiply impressions, taps,
installs and spend by the number of syncs.

This test MUST fail while the grain treats NULL storefront as distinct.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy import func, select

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.asa import ASACredential, ASAMetricDaily
from app.models.user import User
from app.services.asa.sync import _record_metric, _upsert_metrics


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_credential(session) -> int:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"dedupe-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Dedupe",
    )
    session.add(user)
    await session.flush()

    cred = ASACredential(
        user_id=user.id,
        name="cred",
        client_id_ciphertext=encrypt_value("cid"),
        team_id_ciphertext=encrypt_value("tid"),
        key_id="k",
        private_key_ciphertext=encrypt_value("pk"),
    )
    session.add(cred)
    await session.flush()
    return cred.id


def test_resync_does_not_duplicate_null_storefront_rows() -> None:
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            credential_id = await _seed_credential(session)

            # Exactly what Apple returns today: no countryOrRegion key, because
            # _selector never asks for a groupBy.
            granularity_row = {
                "date": "2026-08-01",
                "impressions": 1000,
                "taps": 100,
                "installs": 10,
                "localSpend": {"amount": "25.50", "currency": "USD"},
            }
            row = _record_metric(
                dim_kind="CAMPAIGN",
                dim_id=424242,
                app_adam_id="99887766",
                credential_id=credential_id,
                granularity_row=granularity_row,
            )
            assert row["storefront"] is None, "no groupBy => storefront is NULL"

            # Two sync runs over the same reporting window.
            await _upsert_metrics(session, [dict(row)])
            await _upsert_metrics(session, [dict(row)])
            await session.commit()

            scope = (
                ASAMetricDaily.dim_kind == "CAMPAIGN",
                ASAMetricDaily.dim_id == 424242,
                ASAMetricDaily.date == date(2026, 8, 1),
            )
            count = (
                await session.execute(
                    select(func.count()).select_from(ASAMetricDaily).where(*scope)
                )
            ).scalar_one()
            total_spend = (
                await session.execute(
                    select(func.sum(ASAMetricDaily.spend_amount)).where(*scope)
                )
            ).scalar_one()

            assert count == 1, (
                f"re-sync duplicated the grain: {count} rows for one "
                f"(dim_kind, dim_id, date) -- every sync inflates the rollups"
            )
            # The rollups read through func.sum, so duplication is money-wrong.
            assert float(total_spend) == 25.50, (
                f"spend inflated to {total_spend} after a second sync of the "
                f"same window (expected 25.50)"
            )

    asyncio.run(run())
