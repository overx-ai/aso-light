from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import encrypt_value, hash_password
from app.data.stopwords import stopwords_for_locales
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordTracking
from app.models.keyword_intel import KeywordIntelCache
from app.models.user import User
from app.services.keyword_intel import free
from app.services.keyword_intel.base import KeywordIntel, upsert_intel
from app.services.keyword_intel.free import (
    FreeIntelProvider,
    FreeKeywordIntel,
    blend_popularity,
    extract_listing_terms,
    organic_difficulty,
    pick_variant,
    prefix_depth_score,
)
from app.services.keyword_intel.packer import PackCandidate, pack_storefront
from app.services.keyword_intel.service import run_providers
from app.services.keywords.cross_localization import (
    get_indexed_locales_for_territory,
)
from app.services.metadata.validation import FIELD_CHAR_LIMITS
from tests._async_harness import run_async


class _FakeHints:
    """Hints keyed by exact prefix; anything else returns no hints."""

    def __init__(self, by_prefix: dict[str, list[str]]) -> None:
        self.by_prefix = by_prefix
        self.calls: list[tuple[str, str]] = []

    async def get_suggestions(self, term: str, country: str = "us", **_: object) -> list[str]:
        self.calls.append((term, country))
        return self.by_prefix.get(term, [])


@pytest.fixture(autouse=True)
def _clear_hint_cache():
    free.clear_hint_cache()
    yield
    free.clear_hint_cache()


def test_prefix_depth_score_is_monotonic():
    term_len = 8
    scores = [
        prefix_depth_score(p, term_len, position=pos, list_size=10)
        for p in range(1, term_len + 1)
        for pos in (0, 9)
    ]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)
    assert scores[0] == 100
    assert scores[-1] > 0


def test_popularity_stops_on_first_hit_and_misses_score_zero():
    hints = _FakeHints({"me": ["meditation", "meme"], "x": ["xbox"]})
    intel = FreeKeywordIntel(suggestions=hints, search=None)

    hit = run_async(intel.popularity("meditation", "US"))
    assert hit > 0
    assert [c[0] for c in hints.calls] == ["m", "me"]

    miss = run_async(intel.popularity("zzq", "US"))
    assert miss == 0


def test_difficulty_rises_with_rating_counts_and_title_matches():
    titles_none = ["Calm", "Headspace", "Balance"]
    titles_all = ["Sleep Timer", "Sleep Timer Pro", "Best Sleep Timer"]
    low = organic_difficulty([10, 20, 30], titles_none, "sleep timer")
    more_ratings = organic_difficulty([10_000, 50_000, 90_000], titles_none, "sleep timer")
    more_titles = organic_difficulty([10, 20, 30], titles_all, "sleep timer")
    assert low < more_ratings
    assert low < more_titles
    assert 0 <= low <= 100 and more_ratings <= 100


def test_packer_respects_limit_name_words_and_cross_locale_repeats():
    locales = get_indexed_locales_for_territory("US")[:3]
    words = [f"word{i:02d}" for i in range(40)] + ["sleep", "timer"]
    candidates = {
        loc: [PackCandidate(term=w, popularity=50 + i % 7, difficulty=10) for i, w in enumerate(words)]
        for loc in locales
    }
    fields = pack_storefront(
        candidates,
        names_by_locale={locales[0]: "Sleep Timer"},
        subtitles_by_locale={locales[0]: "Word00 relax"},
    )
    seen: list[str] = []
    for loc in locales:
        value = fields[loc]
        assert len(value) <= FIELD_CHAR_LIMITS["keywords"]
        assert " " not in value
        terms = value.split(",") if value else []
        assert not {"sleep", "timer", "word00"} & set(terms)
        seen.extend(terms)
    assert len(seen) == len(set(seen))
    assert seen


def test_blend_uses_hints_at_or_below_asa_floor():
    assert blend_popularity(asa=None, hints=40) == (40, "hints")
    assert blend_popularity(asa=5, hints=40) == (40, "hints")
    assert blend_popularity(asa=62, hints=40) == (62, "asa")


def test_variant_picker_picks_best_and_flags_all_miss():
    pick = pick_variant({"schlaf timer": 30, "einschlafhilfe": 70, "schlafuhr": 0})
    assert pick.best == "einschlafhilfe"
    assert pick.score == 70
    assert set(pick.alternatives) == {"schlaf timer", "schlafuhr"}
    assert pick.not_searched_here is False

    miss = pick_variant({"a": 0, "b": 0})
    assert miss.not_searched_here is True


def test_own_listing_extraction_drops_stopwords_and_brand():
    terms = extract_listing_terms(
        name="Zenly: Sleep Timer",
        subtitle="Relax and fall asleep",
        keywords="white noise,rain",
        description="The best sleep timer for the night.",
        stopwords=stopwords_for_locales(["en-US"]),
        brand="zenly",
    )
    assert {"sleep", "timer", "sleep timer", "white noise", "rain", "fall asleep"} <= terms
    assert "zenly" not in terms
    assert not any(t in terms for t in ("and", "the", "for"))
    assert not any("zenly" in t.split() for t in terms)


async def _seed_tracked_app(term: str) -> tuple[int, int]:
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        user = User(email=f"free-{suffix}@example.com", password_hash=hash_password("pw-123456"), name="F")
        session.add(user)
        await session.flush()
        cred = ASCCredential(
            user_id=user.id, name="c", issuer_id=f"i-{suffix}", key_id=f"k-{suffix}",
            private_key_encrypted=encrypt_value("fixture"),
        )
        session.add(cred)
        await session.flush()
        app = App(credential_id=cred.id, asc_app_id=f"9{suffix}", bundle_id="b", name="Zen Timer", platform="ios")
        keyword = Keyword(text=term, locale="en-US")
        session.add_all([app, keyword])
        await session.flush()
        session.add(KeywordTracking(app_id=app.id, keyword_id=keyword.id))
        await upsert_intel(session, app.id, [
            KeywordIntel(keyword=term, locale="US", source="asa_recommendations", volume_score=5),
        ])
        await session.commit()
        return app.id, keyword.id


def test_free_provider_writes_blended_row_and_keyword_popularity(monkeypatch):
    term = f"focus{uuid.uuid4().hex[:4]}"

    class _FakeSearch:
        async def search_apps(self, term, country="us", limit=200, *, client=None):
            return [{"name": f"{term} app", "rating_count": 1000}, {"name": "Other", "rating_count": 10}]

    monkeypatch.setattr(
        free, "_default_services",
        lambda: (_FakeHints({term[:2]: [term]}), _FakeSearch()),
    )
    monkeypatch.setattr(
        "app.services.keyword_intel.service.PROVIDER_FACTORIES", (FreeIntelProvider,),
    )

    async def go() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app_id, keyword_id = await _seed_tracked_app(term)
        async with async_session_factory() as session:
            result = await run_providers(session, app_id)
            await session.commit()
        assert result.by_source == {FreeIntelProvider.name: 1}
        async with async_session_factory() as session:
            row = (await session.execute(select(KeywordIntelCache).where(
                KeywordIntelCache.app_id == app_id,
                KeywordIntelCache.source == FreeIntelProvider.name,
            ))).scalar_one()
            keyword = (await session.execute(select(Keyword).where(Keyword.id == keyword_id))).scalar_one()
        assert row.locale == "US"
        assert row.popularity_source == "hints"
        assert row.volume_score and row.volume_score > 5
        assert row.difficulty_score is not None
        assert keyword.popularity == row.volume_score
        assert keyword.popularity_updated_at is not None

    run_async(go())


def test_competitor_keywords_alias_is_gone_and_free_tools_registered():
    async def names() -> set[str]:
        getter = getattr(mcp, "list_tools", None) or getattr(mcp, "_list_tools")
        return {t.name for t in await getter()}

    registered = run_async(names())
    assert "keywords_add_competitor_keywords" not in registered
    assert {"keyword_intel_score_free", "keyword_intel_pack_fields"} <= registered
