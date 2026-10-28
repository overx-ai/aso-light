from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.listing import listing_gaps as rest_listing_gaps
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.territory import Territory
from app.models.user import User
from app.schemas.keyword_intel import VariantPickOut
from app.schemas.listing import ListingProposal
from app.services.keyword_intel.free import FreeKeywordIntel, clear_hint_cache
from app.services.keyword_intel.tokenize import words
from app.services.listing.errors import ListingNotBrainstormedError, NoProposalDraftError
from app.services.listing.proposals import is_valid_proposal, parse_proposals
from app.services.listing.service import ListingOptimizerService
from app.services.metadata.coloring import classify_keyword
from app.services.metadata.translate import (
    PROPOSALS_FIELD_KIND,
    VARIANTS_FIELD_KIND,
    _build_system_prompt,
)
from app.services.metadata.validation import FIELD_CHAR_LIMITS
from tests._async_harness import run_async

ALL_EDITABLE = [
    "name", "subtitle", "privacy_policy_url", "description", "keywords",
    "promotional_text", "whats_new", "marketing_url", "support_url",
]
HINTS = ["meal planner", "calorie counter", "diet", "fasting", "food diary", "macro tracker"]


class FakeSuggestions:
    async def get_suggestions(self, term: str, country: str = "us") -> list[str]:
        return [h for h in HINTS if h.startswith(term)] or HINTS


class FakeSearch:
    async def search_apps(self, term: str, country: str = "us", limit: int = 10) -> list[dict]:
        return [
            {"name": "Meal Planner Pro", "rating_count": 10},
            {"name": "Fasting Timer", "rating_count": 5},
        ]


class FakeTranslator:
    model_name = "fake:model"

    def __init__(self, proposals: str = "[]") -> None:
        self.proposals = proposals
        self.calls: list[str] = []

    async def translate(self, text, source_locale, target_locale, field_kind, brand_allowlist=None):
        self.calls.append(field_kind)
        if field_kind == "listing_proposals":
            return self.proposals
        if field_kind == VARIANTS_FIELD_KIND:
            return f"{text} de\n{text}"
        return f"{text} DE"[: FIELD_CHAR_LIMITS.get(field_kind, 4000)]


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _make_app(session, label: str) -> App:
    suffix = uuid.uuid4().hex[:8]
    user = User(email=f"{label}-{suffix}@example.com", password_hash=hash_password("pw-123456"), name=label)
    session.add(user)
    await session.flush()
    cred = ASCCredential(
        user_id=user.id, name=label, issuer_id=f"i-{suffix}", key_id=f"k-{suffix}",
        private_key_encrypted=encrypt_value("fixture"),
    )
    session.add(cred)
    await session.flush()
    app = App(
        credential_id=cred.id, asc_app_id=f"adam-{suffix}", bundle_id=f"ai.overx.{suffix}",
        name="Kcal Calorie Counter", platform="ios",
    )
    session.add(app)
    await session.flush()
    return app


async def _seed(editable_fields: list[str] = ALL_EDITABLE, de_keywords: str = "diät") -> int:
    await _ensure_schema()
    async with async_session_factory() as session:
        app = await _make_app(session, "listing-owner")
        for locale, name, subtitle, keywords in (
            ("en-US", "Kcal Calorie Counter", "Food diary", "diet,tracker"),
            ("de-DE", "Kcal Kalorienzähler", "Ernährung", de_keywords),
        ):
            session.add_all([
                AppMetadataLocalization(
                    app_id=app.id, kind="app_info", asc_localization_id=f"ai-{locale}",
                    asc_parent_id="info", locale=locale, name=name, subtitle=subtitle,
                ),
                AppMetadataLocalization(
                    app_id=app.id, kind="version", asc_localization_id=f"v-{locale}",
                    asc_parent_id="ver", locale=locale, keywords=keywords,
                    description="Track calories", promotional_text="New",
                ),
            ])
        session.add(AppMetadataState(
            app_id=app.id, editable_version_id="ver", app_info_id="info",
            editable_version_state="PREPARE_FOR_SUBMISSION" if "keywords" in editable_fields
            else "READY_FOR_DISTRIBUTION",
            editable_fields_json=editable_fields,
        ))
        await session.commit()
        return app.id


def _service(session, app: App, translator: FakeTranslator | None = None) -> ListingOptimizerService:
    return ListingOptimizerService(
        session, app,
        translator=translator or FakeTranslator(),
        intel=FreeKeywordIntel(FakeSuggestions(), FakeSearch()),
    )


@pytest.fixture(autouse=True)
def _fresh_hint_cache():
    clear_hint_cache()
    yield
    clear_hint_cache()


def test_missing_never_contains_a_using_term():
    async def go():
        app_id = await _seed()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            out = await _service(session, app).brainstorm()
        using = {t.term for t in out.using}
        missing = {t.term for t in out.missing}
        assert missing
        assert not missing & using
        assert "diet" not in missing and "food diary" not in missing
    run_async(go())


def test_missing_localized_fires_when_source_ranks_low_and_variant_absent():
    assert classify_keyword(
        "calorie counter", "Kcal", "Ernährung", "diät",
        variant="kalorienzähler", source_rank=None,
    ) == "missing_localized"
    assert classify_keyword(
        "calorie counter", "Kcal Kalorienzähler", "Ernährung", "diät",
        variant="kalorienzähler", source_rank=None,
    ) == "title"
    assert classify_keyword(
        "calorie counter", "Kcal", "Ernährung", "diät",
        variant="kalorienzähler", source_rank=3,
    ) == "none"


def test_gaps_board_flags_de_market_from_stored_variant_and_rank():
    async def go():
        app_id = await _seed()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            territory = (await session.execute(
                select(Territory).where(Territory.code == "DE")
            )).scalar_one_or_none()
            if territory is None:
                territory = Territory(code="DE", name="Germany", currency_code="EUR")
                session.add(territory)
                await session.flush()
            keyword = Keyword(text=f"macro tracker {uuid.uuid4().hex[:6]}", locale="en-US")
            session.add(keyword)
            await session.flush()
            tracking = KeywordTracking(app_id=app.id, keyword_id=keyword.id)
            session.add(tracking)
            await session.flush()
            session.add(KeywordRanking(tracking_id=tracking.id, territory_id=territory.id, rank=None))
            await session.flush()

            service = _service(session, app)
            await service.store_variants([VariantPickOut(
                source_term=keyword.text, locale="de-DE", best="makrozähler",
                score=40, not_searched_here=False,
            )])
            board = await service.gaps()
        de = next(c for c in board.cells if c.keyword == keyword.text and c.locale == "de-DE")
        assert de.placement == "missing_localized"
        assert de.variant == "makrozähler"
    run_async(go())


def test_parse_proposals_drops_invalid_sets():
    raw = "```json\n" + json.dumps([
        {"name": "Kcal: Meal Planner", "subtitle": "Fasting & macros", "keywords": "diet,tracker"},
        {"name": "K" * 31, "subtitle": "x", "keywords": "a"},
        {"name": "Kcal Diet", "subtitle": "Diet plans", "keywords": "fasting"},
        {"name": "Kcal", "subtitle": "Food", "keywords": ",".join(["word"] * 30)},
        {"name": "Kcal Planner"},
    ]) + "\n```"
    proposals = parse_proposals(raw)
    assert [p.name for p in proposals] == ["Kcal: Meal Planner"]
    assert parse_proposals("not json") == []


def test_propose_returns_only_valid_proposals():
    async def go():
        app_id = await _seed()
        translator = FakeTranslator(json.dumps([
            {"name": "Kcal: Meal Planner", "subtitle": "Fasting & macros", "keywords": "diet,food"},
            {"name": "Kcal Meal Planner", "subtitle": "Planner for meals", "keywords": "diet"},
            {"name": "Kcal Fasting", "subtitle": "Calorie counter", "keywords": "x" * 101},
        ]))
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            service = _service(session, app, translator)
            await service.brainstorm()
            out = await service.propose()
        assert [p.name for p in out.proposals] == ["Kcal: Meal Planner"]
        for item in out.proposals:
            assert is_valid_proposal(ListingProposal(
                name=item.name, subtitle=item.subtitle, keywords=item.keywords,
            ))
            assert len(item.name) <= 30 and len(item.subtitle) <= 30 and len(item.keywords) <= 100
            fields = [words(item.name), words(item.subtitle), item.keywords.split(",")]
            flat = [w for f in fields for w in f]
            assert len(flat) == len(set(flat))
        assert "listing_proposals" in translator.calls
    run_async(go())


@pytest.mark.parametrize("editable", [
    ["name", "subtitle", "privacy_policy_url", "promotional_text"],
    ["promotional_text"],
])
def test_localize_never_writes_outside_editable_fields(editable):
    async def go():
        app_id = await _seed(editable_fields=editable)
        proposal = ListingProposal(name="Kcal Meal Planner", subtitle="Fasting and macros", keywords="diet,food")
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            out = await _service(session, app).localize(proposal)
        assert out.bulk_previews or editable == ["promotional_text"]
        for payload in out.bulk_previews:
            assert payload.field in editable
            assert set(payload.values_by_locale) == set(payload.target_locales)
        assert "keywords" in out.locked_fields
    run_async(go())


def test_export_returns_per_locale_fields_and_overlays_proposal():
    async def go():
        app_id = await _seed()
        proposal = ListingProposal(name="Kcal Meal Planner", subtitle="Fasting and macros", keywords="diet,food")
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            service = _service(session, app)
            current = await service.export("current")
            await service.localize(proposal)
            drafted = await service.export("proposal")
        assert set(current.locales) == {"en-US", "de-DE"}
        assert current.locales["en-US"].keywords == "diet,tracker"
        assert current.locales["de-DE"].promotional_text == "New"
        assert drafted.locales["en-US"].name == "Kcal Meal Planner"
        assert drafted.locales["de-DE"].description == "Track calories"
    run_async(go())


def test_listing_route_rejects_another_users_app():
    async def go():
        app_id = await _seed()
        async with async_session_factory() as session:
            intruder = await _make_app(session, "listing-intruder")
            cred = await session.get(ASCCredential, intruder.credential_id)
            await session.commit()
            with pytest.raises(HTTPException) as exc:
                await rest_listing_gaps(
                    app_id, current_user={"user_id": str(cred.user_id)}, session=session,
                )
        assert exc.value.status_code in {403, 404}
    run_async(go())


def test_proposals_kind_gets_the_proposals_prompt_not_a_translation_prompt():
    prompt = _build_system_prompt(PROPOSALS_FIELD_KIND, "en-US", 4000, ["Kcal"])
    assert "JSON array" in prompt
    assert "translating" not in prompt


def test_propose_and_export_refuse_before_their_prerequisites():
    async def go():
        app_id = await _seed()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            service = _service(session, app)
            with pytest.raises(ListingNotBrainstormedError):
                await service.propose()
            with pytest.raises(NoProposalDraftError):
                await service.export("proposal")
    run_async(go())
