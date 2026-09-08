"""Re-syncing an analytics report must not duplicate rows.

This is the regression the ``AppAnalyticsDaily`` grain is shaped around.
``ASAMetricDaily`` dedupes on ``(dim_kind, dim_id, date, storefront)`` with
``storefront`` nullable; NULL never equals NULL in a unique index, so its
ON CONFLICT arbiter never matches and each re-sync appends a duplicate that
inflates every ``func.sum`` rollup (see test_asa_metric_upsert_dedupe.py).

Here every column in the grain is NOT NULL -- dimensions carry an empty-string
sentinel -- so the arbiter always matches. These tests MUST fail if any grain
column becomes nullable again, or if ``dim_hash`` stops covering a dimension.

Fixtures use Apple's real long format (Event + Counts), not a wide-column
invention.
"""
from __future__ import annotations

import asyncio
import gzip
import uuid
from datetime import date

from sqlalchemy import func, select

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.analytics import KIND_ENGAGEMENT, AppAnalyticsDaily, dim_hash
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.services.analytics import parse_segment, to_fact_rows, upsert_rows

HEADER = (
    "Date\tApp Name\tApp Apple Identifier\tEvent\tPage Type\tPage Title\t"
    "Source Type\tSource Info\tCampaign\tEngagement Type\tDevice\t"
    "Platform Version\tTerritory\tCounts\tUnique Counts"
)


def _segment(rows: list[str]) -> bytes:
    """A gzipped TSV segment in Apple's real format."""
    return gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode("utf-8"))


def _row(day: str, counts, unique, *, page_title: str = "No page",
         territory: str = "USA") -> str:
    return (
        f"{day}\tApp\t123\tImpression\tNo page\t{page_title}\t"
        f"App Store search\t\t\t\tiPhone\tiOS 26.6\t{territory}\t"
        f"{counts}\t{unique}"
    )


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_app(session) -> App:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"an-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Analytics",
    )
    session.add(user)
    await session.flush()

    cred = ASCCredential(
        user_id=user.id,
        name="cred",
        issuer_id=f"iss-{suffix}",
        key_id=f"key-{suffix}",
        private_key_encrypted=encrypt_value("pk"),
    )
    session.add(cred)
    await session.flush()

    app = App(
        credential_id=cred.id,
        asc_app_id=f"asc-{suffix}",
        name="App",
        bundle_id=f"com.x.{suffix}",
        platform="IOS",
    )
    session.add(app)
    await session.flush()
    return app


def test_resync_updates_instead_of_duplicating() -> None:
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            app = await _seed_app(session)

            parsed = parse_segment(
                _segment([_row("2026-08-01", 1000, 900)]),
                report_kind=KIND_ENGAGEMENT,
            )
            assert len(parsed) == 1
            rows = to_fact_rows(
                parsed,
                app_id=app.id,
                credential_id=app.credential_id,
                report_kind=KIND_ENGAGEMENT,
            )

            # Two sync runs over the same reporting window.
            await upsert_rows(session, rows)
            await upsert_rows(session, rows)
            await session.commit()

            scope = (
                AppAnalyticsDaily.app_id == app.id,
                AppAnalyticsDaily.date == date(2026, 8, 1),
            )
            count = (
                await session.execute(
                    select(func.count()).select_from(AppAnalyticsDaily).where(*scope)
                )
            ).scalar_one()
            impressions = (
                await session.execute(
                    select(func.sum(AppAnalyticsDaily.impressions)).where(*scope)
                )
            ).scalar_one()

            assert count == 1, f"re-sync duplicated the grain: {count} rows"
            assert impressions == 1000, (
                f"impressions inflated to {impressions} after a second sync"
            )

    asyncio.run(run())


def test_empty_dimension_still_dedupes() -> None:
    """The empty-string sentinel is what makes the grain total.

    A row with no product page has page_title "". If that were NULL, this is
    the exact case that would duplicate on every re-sync.
    """
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            app = await _seed_app(session)
            rows = to_fact_rows(
                parse_segment(
                    _segment([_row("2026-08-02", 10, 9, page_title="", territory="")]),
                    report_kind=KIND_ENGAGEMENT,
                ),
                app_id=app.id,
                credential_id=app.credential_id,
                report_kind=KIND_ENGAGEMENT,
            )
            assert rows[0]["page_title"] == ""
            assert rows[0]["territory"] == ""

            await upsert_rows(session, rows)
            await upsert_rows(session, rows)
            await session.commit()

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AppAnalyticsDaily)
                    .where(
                        AppAnalyticsDaily.app_id == app.id,
                        AppAnalyticsDaily.date == date(2026, 8, 2),
                    )
                )
            ).scalar_one()
            assert count == 1, "all-empty dimensions duplicated on re-sync"

    asyncio.run(run())


def test_distinct_dimensions_stay_distinct() -> None:
    """Dedupe must not over-collapse: different pages are different rows."""
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            app = await _seed_app(session)
            rows = to_fact_rows(
                parse_segment(
                    _segment([
                        _row("2026-08-03", 10, 9, page_title="cpp-a"),
                        _row("2026-08-03", 20, 18, page_title="cpp-b"),
                    ]),
                    report_kind=KIND_ENGAGEMENT,
                ),
                app_id=app.id,
                credential_id=app.credential_id,
                report_kind=KIND_ENGAGEMENT,
            )
            assert rows[0]["dim_hash"] != rows[1]["dim_hash"]

            await upsert_rows(session, rows)
            await session.commit()

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AppAnalyticsDaily)
                    .where(
                        AppAnalyticsDaily.app_id == app.id,
                        AppAnalyticsDaily.date == date(2026, 8, 3),
                    )
                )
            ).scalar_one()
            assert count == 2, "distinct product pages collapsed into one row"

    asyncio.run(run())


def test_dim_hash_is_order_stable() -> None:
    """Hash depends on the dimension tuple, not dict insertion order."""
    a = dim_hash({"territory": "USA", "page_title": "x"})
    b = dim_hash({"page_title": "x", "territory": "USA"})
    assert a == b
    assert a != dim_hash({"territory": "GBR", "page_title": "x"})
