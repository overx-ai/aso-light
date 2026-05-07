"""Integrity tests for the ASA models.

These exercise the constraints that matter — composite uniqueness on the
fact table grain, the XOR CHECK on negative keywords, and the derived
`scope` property — rather than tautological assertions about table names.

Tests are sync `def` functions that drive an asyncio runtime via
`asyncio.run(...)`. Each test isolates its own session and rolls back on
expected failures so the next test starts clean.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASAMetricDaily,
    ASANegativeKeyword,
    ASAOrg,
    ASASyncOperation,
)
from app.models.user import User


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def test_all_nine_asa_tables_register():
    async def go() -> set[str]:
        await _ensure_schema()
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: set(inspect(c).get_table_names()),
            )

    names = asyncio.run(go())
    expected = {
        "asa_credentials", "asa_orgs", "asa_campaigns", "asa_ad_groups",
        "asa_keywords", "asa_negative_keywords", "asa_search_terms",
        "asa_metric_daily", "asa_sync_operations",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


# ---------------------------------------------------------------------------
# Helpers — fresh fixture chain per test
# ---------------------------------------------------------------------------


async def _make_user_and_credential(session) -> tuple[int, int]:
    user = User(
        email=f"asa-test-{uuid.uuid4().hex}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="ASA Test",
    )
    session.add(user)
    await session.flush()
    cred = ASACredential(
        user_id=user.id,
        name="c",
        client_id_ciphertext=encrypt_value("c"),
        team_id_ciphertext=encrypt_value("t"),
        key_id="K",
        private_key_ciphertext=encrypt_value("p"),
    )
    session.add(cred)
    await session.flush()
    return user.id, cred.id


async def _make_campaign_and_ad_group(session, cred_id: int) -> tuple[int, int]:
    # asa_org_id and asa_campaign_id collide on the (credential_id, asa_org_id)
    # and (org_id, asa_campaign_id) UNIQUE constraints across runs that share
    # the on-disk DB; bump per-test with uuid-derived ints.
    asa_org_id = uuid.uuid4().int >> 96
    asa_campaign_id = uuid.uuid4().int >> 96
    asa_ad_group_id = uuid.uuid4().int >> 96
    org = ASAOrg(
        credential_id=cred_id,
        asa_org_id=asa_org_id,
        name="o", currency="USD", timezone="UTC",
    )
    session.add(org)
    await session.flush()
    camp = ASACampaign(
        org_id=org.id, asa_campaign_id=asa_campaign_id, app_adam_id="999",
        name="c", status="ENABLED",
    )
    session.add(camp)
    await session.flush()
    ag = ASAAdGroup(
        campaign_id=camp.id, asa_ad_group_id=asa_ad_group_id,
        name="a", status="ENABLED",
    )
    session.add(ag)
    await session.flush()
    return camp.id, ag.id


async def _expect_integrity_error(session) -> bool:
    """Try to commit; return True if IntegrityError was raised, then rollback."""
    try:
        await session.commit()
        return False
    except IntegrityError:
        await session.rollback()
        return True


# ---------------------------------------------------------------------------
# CHECK constraint on asa_negative_keywords
# ---------------------------------------------------------------------------


def test_negative_keyword_check_rejects_both_null():
    async def go() -> bool:
        await _ensure_schema()
        async with async_session_factory() as session:
            _, cred_id = await _make_user_and_credential(session)
            await _make_campaign_and_ad_group(session, cred_id)
            session.add(ASANegativeKeyword(
                campaign_id=None, ad_group_id=None,
                asa_negative_keyword_id=1, text="bad", match_type="EXACT",
            ))
            return await _expect_integrity_error(session)

    assert asyncio.run(go()), "CHECK constraint did not reject both-NULL row"


def test_negative_keyword_check_rejects_both_set():
    async def go() -> bool:
        await _ensure_schema()
        async with async_session_factory() as session:
            _, cred_id = await _make_user_and_credential(session)
            camp_id, ag_id = await _make_campaign_and_ad_group(session, cred_id)
            session.add(ASANegativeKeyword(
                campaign_id=camp_id, ad_group_id=ag_id,
                asa_negative_keyword_id=2, text="bad", match_type="EXACT",
            ))
            return await _expect_integrity_error(session)

    assert asyncio.run(go()), "CHECK constraint did not reject both-set row"


def test_negative_keyword_scope_property_derives_from_fk():
    async def go() -> tuple[str, str]:
        await _ensure_schema()
        async with async_session_factory() as session:
            _, cred_id = await _make_user_and_credential(session)
            camp_id, ag_id = await _make_campaign_and_ad_group(session, cred_id)
            campaign_neg = ASANegativeKeyword(
                campaign_id=camp_id, ad_group_id=None,
                asa_negative_keyword_id=3, text="cn", match_type="EXACT",
            )
            ad_group_neg = ASANegativeKeyword(
                campaign_id=None, ad_group_id=ag_id,
                asa_negative_keyword_id=4, text="agn", match_type="BROAD",
            )
            session.add_all([campaign_neg, ad_group_neg])
            await session.commit()
            return campaign_neg.scope, ad_group_neg.scope

    cn_scope, ag_scope = asyncio.run(go())
    assert cn_scope == "CAMPAIGN"
    assert ag_scope == "AD_GROUP"


# ---------------------------------------------------------------------------
# UNIQUE constraint on asa_metric_daily grain
# ---------------------------------------------------------------------------


def test_metric_daily_grain_uniqueness():
    async def go() -> bool:
        await _ensure_schema()
        async with async_session_factory() as session:
            _, cred_id = await _make_user_and_credential(session)
            _, ag_id = await _make_campaign_and_ad_group(session, cred_id)
            kw = ASAKeyword(
                ad_group_id=ag_id, asa_keyword_id=1,
                text="kanban", match_type="EXACT", status="ENABLED",
            )
            session.add(kw)
            await session.flush()
            d = date(2026, 5, 8)
            session.add(ASAMetricDaily(
                dim_kind="KEYWORD", dim_id=kw.id, app_adam_id="999",
                date=d, storefront="US", impressions=100, taps=10, installs=1,
                spend_amount=5, spend_currency="USD",
            ))
            await session.commit()
            session.add(ASAMetricDaily(
                dim_kind="KEYWORD", dim_id=kw.id, app_adam_id="999",
                date=d, storefront="US", impressions=200, taps=20, installs=2,
                spend_amount=10, spend_currency="USD",
            ))
            return await _expect_integrity_error(session)

    assert asyncio.run(go()), "UNIQUE on (dim_kind, dim_id, date, storefront) not enforced"


# ---------------------------------------------------------------------------
# Sync operation defaults + relationship
# ---------------------------------------------------------------------------


def test_sync_operation_defaults_and_relationships():
    async def go() -> tuple[bool, list | None, list | None, int]:
        await _ensure_schema()
        async with async_session_factory() as session:
            uid, cred_id = await _make_user_and_credential(session)
            op = ASASyncOperation(
                credential_id=cred_id, user_id=uid, status="running",
            )
            session.add(op)
            await session.commit()
            await session.refresh(op)
            return op.full_backfill, op.steps, op.error_log, op.credential_id

    full_backfill, steps, error_log, cred_id_back = asyncio.run(go())
    assert full_backfill is False
    assert steps is None
    assert error_log is None
    assert cred_id_back > 0
