"""Cross-tenant scoping for app analytics reads.

Two users hold their own ASC credential and their own App row, and both apps
point at the SAME Apple app id — the exact shape that leaked ASA metrics before
``credential_id`` scoping was added there (see test_asa_analytics_scoping.py).

Every read helper must return only the calling user's rows. These tests MUST
fail if the ``credential_id.in_(owned_credential_ids(user_id))`` filter is
dropped from any query in app.services.analytics.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.analytics import (
    KIND_DOWNLOADS,
    KIND_ENGAGEMENT,
    AppAnalyticsDaily,
    dim_hash,
)
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.services.analytics import (
    cpp_performance,
    download_rows,
    engagement_rows,
)

SHARED_ASC_APP_ID = "1234567890"


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_tenant(session, *, impressions: int, downloads: int) -> dict:
    """An independent tenant whose App targets the SHARED Apple app id."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"scope-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Scope",
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
        asc_app_id=SHARED_ASC_APP_ID,
        name="Shared",
        bundle_id=f"com.x.{suffix}",
        platform="IOS",
    )
    session.add(app)
    await session.flush()

    dims = {"territory": "USA", "page_title": "cpp-1"}
    session.add(
        AppAnalyticsDaily(
            app_id=app.id,
            credential_id=cred.id,
            report_kind=KIND_ENGAGEMENT,
            date=date.today(),
            territory="USA",
            page_title="cpp-1",
            dim_hash=dim_hash(dims),
            impressions=impressions,
            page_views=impressions // 2,
        )
    )
    session.add(
        AppAnalyticsDaily(
            app_id=app.id,
            credential_id=cred.id,
            report_kind=KIND_DOWNLOADS,
            date=date.today(),
            territory="USA",
            page_title="cpp-1",
            dim_hash=dim_hash(dims),
            downloads=downloads,
        )
    )
    await session.flush()
    return {"user_id": user.id, "app_id": app.id}


def test_each_tenant_sees_only_its_own_rows() -> None:
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, impressions=1000, downloads=100)
            b = await _seed_tenant(session, impressions=7777, downloads=777)
            await session.commit()

            _, a_eng = await engagement_rows(
                session=session, user_id=a["user_id"], app_id=a["app_id"]
            )
            _, b_eng = await engagement_rows(
                session=session, user_id=b["user_id"], app_id=b["app_id"]
            )
            assert [r["impressions"] for r in a_eng] == [1000]
            assert [r["impressions"] for r in b_eng] == [7777]

            _, a_dl = await download_rows(
                session=session, user_id=a["user_id"], app_id=a["app_id"]
            )
            assert [r["downloads"] for r in a_dl] == [100]

    asyncio.run(run())


def test_foreign_user_id_returns_nothing() -> None:
    """Knowing another tenant's app_id must not be enough to read it."""
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, impressions=1000, downloads=100)
            b = await _seed_tenant(session, impressions=7777, downloads=777)
            await session.commit()

            # User B aims at user A's app id directly.
            _, stolen = await engagement_rows(
                session=session, user_id=b["user_id"], app_id=a["app_id"]
            )
            assert stolen == [], "cross-tenant read of another user's app"

            _, stolen_dl = await download_rows(
                session=session, user_id=b["user_id"], app_id=a["app_id"]
            )
            assert stolen_dl == []

            _, stolen_cpp = await cpp_performance(
                session=session, user_id=b["user_id"], app_id=a["app_id"]
            )
            assert stolen_cpp == []

    asyncio.run(run())


def test_cpp_performance_joins_engagement_and_downloads() -> None:
    """Conversion rate combines two report_kinds without multiplying rows."""
    async def run() -> None:
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, impressions=1000, downloads=100)
            await session.commit()

            _, rows = await cpp_performance(
                session=session, user_id=a["user_id"], app_id=a["app_id"]
            )
            assert len(rows) == 1, "engagement x downloads multiplied rows"
            row = rows[0]
            assert row["page_title"] == "cpp-1"
            assert row["impressions"] == 1000
            assert row["page_views"] == 500
            assert row["downloads"] == 100
            # 100 downloads / 500 page views
            assert str(row["conversion_rate"]) == "0.2000"

    asyncio.run(run())
