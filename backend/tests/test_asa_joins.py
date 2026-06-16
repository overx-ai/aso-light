"""Fixture-based tests for the paid+organic keyword join.

Notes on the local schema vs. the spec sketch:
- `KeywordTracking` is `(app_id, keyword_id)` only — terms live on
  `Keyword.text` and ranks live on `KeywordRanking.rank` keyed by
  `tracking_id`. Tests construct the chain explicitly.
- `App` has no `adam_id` column; the link to ASA fact rows is via
  `App.asc_app_id`, which equals Apple's adam_id and is what the joins
  service compares against `ASAMetricDaily.app_adam_id`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASAMetricDaily,
    ASAOrg,
    ASASearchTerm,
)
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.territory import Territory
from app.models.user import User
from app.services.asa.joins import (
    paid_organic_join,
    suggest_negative_candidates,
    suggest_organic_keywords_to_track,
)


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_world(session) -> tuple[int, str, int, int, int]:
    """Seed user → ASC cred → App with asc_app_id, plus ASA cred/org/campaign/ad_group.

    Returns (app_id, asc_app_id, ad_group_id, user_id, asa_credential_id).
    """
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"join-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Join",
    )
    session.add(user)
    await session.flush()

    asc_cred = ASCCredential(
        user_id=user.id,
        name="asc",
        issuer_id="i",
        key_id="k",
        private_key_encrypted=encrypt_value("k"),
    )
    session.add(asc_cred)
    await session.flush()

    asc_app_id = f"adam-{suffix}"
    app = App(
        credential_id=asc_cred.id,
        asc_app_id=asc_app_id,
        bundle_id="b",
        name="n",
        platform="ios",
    )
    session.add(app)
    await session.flush()

    asa_cred = ASACredential(
        user_id=user.id,
        name="asa",
        client_id_ciphertext=encrypt_value("c"),
        team_id_ciphertext=encrypt_value("t"),
        key_id="K",
        private_key_ciphertext=encrypt_value("p"),
    )
    session.add(asa_cred)
    await session.flush()

    org = ASAOrg(
        credential_id=asa_cred.id,
        asa_org_id=uuid.uuid4().int >> 96,
        name="o",
        currency="USD",
        timezone="UTC",
    )
    session.add(org)
    await session.flush()

    camp = ASACampaign(
        org_id=org.id,
        asa_campaign_id=uuid.uuid4().int >> 96,
        app_id=app.id,
        app_adam_id=asc_app_id,
        name="c",
        status="ENABLED",
    )
    session.add(camp)
    await session.flush()

    ag = ASAAdGroup(
        campaign_id=camp.id,
        asa_ad_group_id=uuid.uuid4().int >> 96,
        name="ag",
        status="ENABLED",
    )
    session.add(ag)
    await session.flush()
    return app.id, asc_app_id, ag.id, user.id, asa_cred.id


async def _ensure_territory(session, code: str = "US") -> int:
    """Make sure a territory row exists for KeywordRanking.territory_id."""
    existing = (
        await session.execute(select_one_territory_stmt(code))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    t = Territory(code=code, name=code, currency_code="USD")
    session.add(t)
    await session.flush()
    return t.id


def select_one_territory_stmt(code: str):
    from sqlalchemy import select

    return select(Territory).where(Territory.code == code)


async def _track_term(
    session, *, app_id: int, term: str, rank: int | None,
    territory_id: int,
) -> None:
    """Create a Keyword + KeywordTracking + (optional) latest KeywordRanking."""
    suffix = uuid.uuid4().hex[:6]
    kw = Keyword(text=term, locale=f"en_us_{suffix}")
    session.add(kw)
    await session.flush()
    kt = KeywordTracking(app_id=app_id, keyword_id=kw.id)
    session.add(kt)
    await session.flush()
    if rank is not None:
        session.add(
            KeywordRanking(
                tracking_id=kt.id,
                territory_id=territory_id,
                rank=rank,
                recorded_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()


def test_paid_organic_join_merges_paid_and_organic():
    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            app_id, asc_app_id, ag_id, user_id, cred_id = await _seed_world(session)
            terr_id = await _ensure_territory(session)
            kw = ASAKeyword(
                ad_group_id=ag_id,
                asa_keyword_id=uuid.uuid4().int >> 96,
                text="kanban app",
                match_type="EXACT",
                status="ENABLED",
            )
            session.add(kw)
            await session.flush()
            today = date.today()
            session.add(
                ASAMetricDaily(
                    dim_kind="KEYWORD",
                    dim_id=kw.id,
                    app_adam_id=asc_app_id,
                    credential_id=cred_id,
                    date=today,
                    impressions=1000,
                    taps=100,
                    installs=10,
                    spend_amount=50,
                    spend_currency="USD",
                )
            )
            await _track_term(
                session, app_id=app_id, term="kanban app", rank=12,
                territory_id=terr_id,
            )
            await _track_term(
                session, app_id=app_id, term="todo list", rank=4,
                territory_id=terr_id,
            )
            await session.commit()
            return await paid_organic_join(
                session=session, app_id=app_id, user_id=user_id, days=30,
            )

    rows = asyncio.run(go())
    by_term = {r["term"]: r for r in rows}
    assert by_term["kanban app"]["paid_impressions_30d"] == 1000
    assert by_term["kanban app"]["organic_rank"] == 12
    assert by_term["todo list"]["paid_impressions_30d"] == 0
    assert by_term["todo list"]["organic_rank"] == 4


def test_suggest_organic_keywords_to_track_filters_min_taps_and_existing():
    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            app_id, asc_app_id, ag_id, user_id, cred_id = await _seed_world(session)
            terr_id = await _ensure_territory(session)
            high = ASASearchTerm(
                ad_group_id=ag_id, text="winning term",
                match_type="BROAD", source="SEARCHTERM",
            )
            low = ASASearchTerm(
                ad_group_id=ag_id, text="weak term",
                match_type="BROAD", source="SEARCHTERM",
            )
            already = ASASearchTerm(
                ad_group_id=ag_id, text="already tracked",
                match_type="BROAD", source="SEARCHTERM",
            )
            session.add_all([high, low, already])
            await session.flush()
            today = date.today()
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM", dim_id=high.id,
                    app_adam_id=asc_app_id, credential_id=cred_id, date=today,
                    taps=50, installs=5, spend_amount=20, spend_currency="USD",
                )
            )
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM", dim_id=low.id,
                    app_adam_id=asc_app_id, credential_id=cred_id, date=today,
                    taps=5, installs=0, spend_amount=2, spend_currency="USD",
                )
            )
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM", dim_id=already.id,
                    app_adam_id=asc_app_id, credential_id=cred_id, date=today,
                    taps=80, installs=8, spend_amount=40, spend_currency="USD",
                )
            )
            await _track_term(
                session, app_id=app_id, term="already tracked", rank=5,
                territory_id=terr_id,
            )
            await session.commit()
            return await suggest_organic_keywords_to_track(
                session=session, app_id=app_id, user_id=user_id, days=30,
                min_taps=20,
            )

    out = asyncio.run(go())
    texts = {r["text"] for r in out}
    assert "winning term" in texts
    assert "weak term" not in texts  # below min_taps
    assert "already tracked" not in texts  # already in keyword_tracking


def test_suggest_negative_candidates_low_conv_high_spend():
    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            app_id, asc_app_id, ag_id, user_id, cred_id = await _seed_world(session)
            wasteful = ASASearchTerm(
                ad_group_id=ag_id, text="wasteful term",
                match_type="BROAD", source="SEARCHTERM",
            )
            session.add(wasteful)
            await session.flush()
            today = date.today()
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM", dim_id=wasteful.id,
                    app_adam_id=asc_app_id, credential_id=cred_id, date=today,
                    taps=1000, installs=2,
                    spend_amount=200, spend_currency="USD",
                )
            )
            await session.commit()
            return await suggest_negative_candidates(
                session=session, app_id=app_id, user_id=user_id, days=30,
                min_spend=10.0, max_conv_rate=0.005,
            )

    out = asyncio.run(go())
    assert any(r["text"] == "wasteful term" for r in out)
    rec = next(r for r in out if r["text"] == "wasteful term")
    assert rec["spend"] >= 10  # Decimal compares fine against int
    assert rec["spend_currency"] == "USD"  # M5: currency now returned
    assert rec["conversion_rate"] <= Decimal("0.005")
