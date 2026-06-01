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
    ASAMetricDaily,
    ASAOrg,
    ASASearchTerm,
)
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordTracking
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.review_theme import ReviewThemeCache
from app.models.user import User
from app.services.growth.recommendations import generate_growth_recommendations


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_app(session) -> tuple[int, str, int]:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"growth-{suffix}@x.x",
        password_hash=hash_password("xxxxxxxx"),
        name="Growth",
    )
    session.add(user)
    await session.flush()

    asc_cred = ASCCredential(
        user_id=user.id,
        name="asc",
        issuer_id="issuer",
        key_id="key",
        private_key_encrypted=encrypt_value("private"),
    )
    session.add(asc_cred)
    await session.flush()

    asc_app_id = f"adam-{suffix}"
    app = App(
        credential_id=asc_cred.id,
        asc_app_id=asc_app_id,
        bundle_id=f"com.example.{suffix}",
        name="Growth App",
        platform="IOS",
    )
    session.add(app)
    await session.flush()

    asa_cred = ASACredential(
        user_id=user.id,
        name="asa",
        client_id_ciphertext=encrypt_value("client"),
        team_id_ciphertext=encrypt_value("team"),
        key_id="K",
        private_key_ciphertext=encrypt_value("asa-private"),
    )
    session.add(asa_cred)
    await session.flush()

    org = ASAOrg(
        credential_id=asa_cred.id,
        asa_org_id=uuid.uuid4().int >> 96,
        name="Org",
        currency="USD",
        timezone="UTC",
    )
    session.add(org)
    await session.flush()

    campaign = ASACampaign(
        org_id=org.id,
        asa_campaign_id=uuid.uuid4().int >> 96,
        app_id=app.id,
        app_adam_id=asc_app_id,
        name="Campaign",
        status="ENABLED",
    )
    session.add(campaign)
    await session.flush()

    ad_group = ASAAdGroup(
        campaign_id=campaign.id,
        asa_ad_group_id=uuid.uuid4().int >> 96,
        name="Ad Group",
        status="ENABLED",
    )
    session.add(ad_group)
    await session.flush()
    return app.id, asc_app_id, ad_group.id


async def _track_keyword(session, app_id: int, text: str, locale: str = "en-US") -> None:
    kw = Keyword(text=text, locale=f"{locale}-{uuid.uuid4().hex[:6]}")
    session.add(kw)
    await session.flush()
    session.add(KeywordTracking(app_id=app_id, keyword_id=kw.id))
    await session.flush()


def test_growth_recommendations_surface_cross_domain_actions():
    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            app_id, asc_app_id, ad_group_id = await _seed_app(session)
            await _track_keyword(session, app_id, "breathing timer")

            paid_winner = ASASearchTerm(
                ad_group_id=ad_group_id,
                text="box breathing",
                match_type="BROAD",
                source="SEARCHTERM",
            )
            waste = ASASearchTerm(
                ad_group_id=ad_group_id,
                text="free ringtone",
                match_type="BROAD",
                source="SEARCHTERM",
            )
            session.add_all([paid_winner, waste])
            await session.flush()
            today = date.today()
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM",
                    dim_id=paid_winner.id,
                    app_adam_id=asc_app_id,
                    date=today,
                    taps=45,
                    installs=9,
                    spend_amount=25,
                    spend_currency="USD",
                )
            )
            session.add(
                ASAMetricDaily(
                    dim_kind="SEARCH_TERM",
                    dim_id=waste.id,
                    app_adam_id=asc_app_id,
                    date=today,
                    taps=900,
                    installs=1,
                    spend_amount=150,
                    spend_currency="USD",
                )
            )
            session.add(
                ReviewThemeCache(
                    app_id=app_id,
                    review_id="r1",
                    theme="bug",
                    severity=5,
                    model="test",
                )
            )
            await session.commit()
            return app_id, await generate_growth_recommendations(
                session=session,
                app_id=app_id,
            )

    app_id, recommendations = asyncio.run(go())
    by_id = {r.id: r for r in recommendations}

    assert "metadata.sync" in by_id
    assert "keywords.expand_tracking" in by_id
    assert "asa.track_paid_winners" in by_id
    assert "asa.add_negative_keywords" in by_id
    assert "reviews.triage_severe" in by_id
    assert all(r.cta_path.startswith(f"/apps/{app_id}/") for r in recommendations)
    assert by_id["asa.track_paid_winners"].evidence["top_term"] == "box breathing"
    assert by_id["reviews.triage_severe"].priority == "high"


def test_growth_recommendations_detect_metadata_keyword_gap_without_sync_prompt():
    async def go():
        await _ensure_schema()
        async with async_session_factory() as session:
            app_id, _, _ = await _seed_app(session)
            await _track_keyword(session, app_id, "wim hof")
            session.add(
                AppMetadataState(
                    app_id=app_id,
                    editable_version_id="v1",
                    editable_version_state="READY_FOR_DISTRIBUTION",
                    app_info_id="info1",
                    editable_fields_json=["promotional_text"],
                )
            )
            session.add(
                AppMetadataLocalization(
                    app_id=app_id,
                    kind="app_info",
                    asc_localization_id="loc-info",
                    asc_parent_id="info1",
                    locale="en-US",
                    name="Breath Focus",
                    subtitle="Sleep and calm",
                )
            )
            session.add(
                AppMetadataLocalization(
                    app_id=app_id,
                    kind="version",
                    asc_localization_id="loc-version",
                    asc_parent_id="v1",
                    locale="en-US",
                    keywords="sleep,focus,calm",
                    promotional_text="Build a daily breathing habit.",
                )
            )
            await session.commit()
            return await generate_growth_recommendations(
                session=session,
                app_id=app_id,
            )

    recommendations = asyncio.run(go())
    ids = {r.id for r in recommendations}

    assert "metadata.sync" not in ids
    assert "metadata.keyword_coverage" in ids
    rec = next(r for r in recommendations if r.id == "metadata.keyword_coverage")
    assert rec.evidence["missing_keywords"] == ["wim hof"]
    assert rec.cta_path.endswith("/metadata")


def test_growth_recommendations_route_is_registered():
    from app.api.v1 import router

    paths = {
        getattr(route, "path", None)
        for route in router.routes
        if getattr(route, "path", None)
    }

    assert "/apps/{app_id}/growth/recommendations" in paths
