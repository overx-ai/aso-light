"""Cross-tenant scoping regression for ASA analytics (C1).

Two users each advertise the SAME Apple app (same ``app_adam_id``) under
their own credential -> org -> campaign chain. Before the ``credential_id``
scoping fix, every analytics query keyed off ``app_adam_id`` alone, so user A
saw user B's metrics and vice versa.

These tests assert that each surface returns ONLY the calling user's rows.
They MUST fail if the ``credential_id.in_(owned_credential_ids(user_id))``
scope filter is removed from the analytics queries.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

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
from app.models.user import User
from app.services.asa.analytics import (
    performance_rows,
    search_term_report_rows,
)
from app.services.asa.joins import paid_organic_join
from app.services.keyword_intel.asa_search_terms import ASASearchTermsProvider


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_tenant(session, *, shared_adam_id: str) -> dict:
    """Seed an independent tenant (user + ASA cred/org/campaign/ad_group/keyword)
    all targeting the SAME ``shared_adam_id``. Returns ids needed by the asserts.
    """
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"tenant-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Tenant",
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

    app = App(
        credential_id=asc_cred.id,
        asc_app_id=shared_adam_id,
        bundle_id=f"b-{suffix}",
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
        app_adam_id=shared_adam_id,
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

    kw = ASAKeyword(
        ad_group_id=ag.id,
        asa_keyword_id=uuid.uuid4().int >> 96,
        text=f"kw-{suffix}",
        match_type="EXACT",
        status="ENABLED",
    )
    session.add(kw)
    await session.flush()

    st = ASASearchTerm(
        ad_group_id=ag.id,
        text=f"term-{suffix}",
        match_type="BROAD",
        source="SEARCHTERM",
    )
    session.add(st)
    await session.flush()

    return {
        "user_id": user.id,
        "app_id": app.id,
        "credential_id": asa_cred.id,
        "campaign_id": camp.id,
        "keyword_id": kw.id,
        "search_term_id": st.id,
    }


def test_performance_report_is_scoped_per_credential():
    """performance_rows for user A must never include user B's metric rows."""
    shared = f"adam-shared-{uuid.uuid4().hex[:8]}"

    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, shared_adam_id=shared)
            b = await _seed_tenant(session, shared_adam_id=shared)
            today = date.today()
            # A's campaign metric
            session.add(ASAMetricDaily(
                dim_kind="CAMPAIGN", dim_id=a["campaign_id"],
                app_adam_id=shared, credential_id=a["credential_id"],
                date=today, impressions=111, taps=11, installs=1,
                spend_amount=10, spend_currency="USD",
            ))
            # B's campaign metric (same app_adam_id, different credential)
            session.add(ASAMetricDaily(
                dim_kind="CAMPAIGN", dim_id=b["campaign_id"],
                app_adam_id=shared, credential_id=b["credential_id"],
                date=today, impressions=999, taps=99, installs=9,
                spend_amount=90, spend_currency="USD",
            ))
            await session.commit()

            _, a_rows = await performance_rows(
                session=session, user_id=a["user_id"],
                app_adam_id=shared, grain="CAMPAIGN", days=30,
            )
            _, b_rows = await performance_rows(
                session=session, user_id=b["user_id"],
                app_adam_id=shared, grain="CAMPAIGN", days=30,
            )
            return a, b, a_rows, b_rows

    a, b, a_rows, b_rows = asyncio.run(go())

    a_cred_ids = {r.credential_id for r in a_rows}
    b_cred_ids = {r.credential_id for r in b_rows}
    # A sees only A; B sees only B — no cross-tenant bleed.
    assert a_cred_ids == {a["credential_id"]}, a_cred_ids
    assert b_cred_ids == {b["credential_id"]}, b_cred_ids
    assert all(r.impressions == 111 for r in a_rows)
    assert all(r.impressions == 999 for r in b_rows)
    assert b["credential_id"] not in a_cred_ids


def test_search_term_report_is_scoped_per_credential():
    """search_term_report_rows for user A must never include user B's spend."""
    shared = f"adam-shared-{uuid.uuid4().hex[:8]}"

    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, shared_adam_id=shared)
            b = await _seed_tenant(session, shared_adam_id=shared)
            today = date.today()
            session.add(ASAMetricDaily(
                dim_kind="SEARCH_TERM", dim_id=a["search_term_id"],
                app_adam_id=shared, credential_id=a["credential_id"],
                date=today, impressions=50, taps=5, installs=1,
                spend_amount=7, spend_currency="USD",
            ))
            session.add(ASAMetricDaily(
                dim_kind="SEARCH_TERM", dim_id=b["search_term_id"],
                app_adam_id=shared, credential_id=b["credential_id"],
                date=today, impressions=500, taps=50, installs=10,
                spend_amount=70, spend_currency="USD",
            ))
            await session.commit()

            _, a_rows = await search_term_report_rows(
                session=session, user_id=a["user_id"], days=30,
            )
            _, b_rows = await search_term_report_rows(
                session=session, user_id=b["user_id"], days=30,
            )
            return a, b, a_rows, b_rows

    a, b, a_rows, b_rows = asyncio.run(go())

    a_term_ids = {r["search_term_id"] for r in a_rows}
    b_term_ids = {r["search_term_id"] for r in b_rows}
    assert a_term_ids == {a["search_term_id"]}, a_term_ids
    assert b_term_ids == {b["search_term_id"]}, b_term_ids
    assert b["search_term_id"] not in a_term_ids


def test_paid_organic_join_is_scoped_per_credential():
    """paid_organic_join must not surface another tenant's paid keyword metrics.

    Both tenants track the same organic term text; only A has paid spend on
    its own keyword. A sees its spend; B (with no owned metric row) sees zero.
    """
    shared = f"adam-shared-{uuid.uuid4().hex[:8]}"
    term_text = f"shared-term-{uuid.uuid4().hex[:6]}"

    async def go():
        await _ensure_schema()
        from app.models.keyword import Keyword, KeywordTracking

        async with async_session_factory() as session:
            a = await _seed_tenant(session, shared_adam_id=shared)
            b = await _seed_tenant(session, shared_adam_id=shared)

            # Both A and B track an organic keyword with identical text.
            for tenant in (a, b):
                kw = Keyword(text=term_text, locale=f"en_us_{uuid.uuid4().hex[:6]}")
                session.add(kw)
                await session.flush()
                session.add(KeywordTracking(app_id=tenant["app_id"], keyword_id=kw.id))
            await session.flush()

            # A's ASA keyword carries the same text and has paid spend.
            a_kw = (await session.get(ASAKeyword, a["keyword_id"]))
            a_kw.text = term_text
            await session.flush()
            session.add(ASAMetricDaily(
                dim_kind="KEYWORD", dim_id=a["keyword_id"],
                app_adam_id=shared, credential_id=a["credential_id"],
                date=date.today(), impressions=1000, taps=100, installs=10,
                spend_amount=50, spend_currency="USD",
            ))
            await session.commit()

            a_join = await paid_organic_join(
                session=session, app_id=a["app_id"], user_id=a["user_id"], days=30,
            )
            b_join = await paid_organic_join(
                session=session, app_id=b["app_id"], user_id=b["user_id"], days=30,
            )
            return a_join, b_join

    a_join, b_join = asyncio.run(go())

    a_row = next(r for r in a_join if r["term"] == term_text)
    b_row = next(r for r in b_join if r["term"] == term_text)
    # A owns the paid metric; B must see zeros (no cross-tenant spend leak).
    assert a_row["paid_spend_30d"] > 0
    assert b_row["paid_impressions_30d"] == 0
    assert b_row["paid_spend_30d"] == 0


def test_keyword_intel_search_terms_provider_is_scoped_per_credential():
    """Path B keyword-intel (ASASearchTermsProvider) must not surface another
    tenant's SEARCH_TERM metrics for the same shared ``app_adam_id``.

    Regression for the second unscoped ``ASAMetricDaily`` reader found in
    keyword_intel/asa_search_terms.py — reachable via the keyword-intel refresh
    route. MUST fail if the credential scope filter is removed there.
    """
    shared = f"adam-shared-{uuid.uuid4().hex[:8]}"

    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            a = await _seed_tenant(session, shared_adam_id=shared)
            b = await _seed_tenant(session, shared_adam_id=shared)
            today = date.today()
            # A's SEARCH_TERM metric (storefront set so the provider keeps it).
            session.add(ASAMetricDaily(
                dim_kind="SEARCH_TERM", dim_id=a["search_term_id"],
                app_adam_id=shared, credential_id=a["credential_id"],
                date=today, storefront="USA",
                impressions=400, taps=40, installs=4,
                spend_amount=20, spend_currency="USD",
            ))
            # B's own SEARCH_TERM metric.
            session.add(ASAMetricDaily(
                dim_kind="SEARCH_TERM", dim_id=b["search_term_id"],
                app_adam_id=shared, credential_id=b["credential_id"],
                date=today, storefront="USA",
                impressions=600, taps=60, installs=6,
                spend_amount=30, spend_currency="USD",
            ))
            await session.commit()

            a_text = (await session.get(ASASearchTerm, a["search_term_id"])).text
            b_text = (await session.get(ASASearchTerm, b["search_term_id"])).text

            provider = ASASearchTermsProvider()
            a_intel = await provider.fetch(app_id=a["app_id"], session=session)
            b_intel = await provider.fetch(app_id=b["app_id"], session=session)
            return a_text, b_text, a_intel, b_intel

    a_text, b_text, a_intel, b_intel = asyncio.run(go())

    a_keywords = {row.keyword for row in a_intel}
    b_keywords = {row.keyword for row in b_intel}
    # Each tenant sees only its own search term, never the other's.
    assert a_text in a_keywords
    assert b_text not in a_keywords
    assert b_text in b_keywords
    assert a_text not in b_keywords
