"""Tests for the keyword-analysis hardening fix set.

Covers:
* I4 — ``_normalize_popularity`` boundary bug (5 must not inflate to 100).
* I1 — per-user rate-limit dependency returns 429 after the configured budget.
* I2 — competitor-keyword check caps the iTunes fan-out and bounds concurrency.
* I3 — ranking refresh caps (keyword x territory) work and bounds concurrency.
* M5 — ``add_competitor`` rejects a non-numeric ``asc_app_id``.

Backend convention: keep the pytest entrypoint sync and drive coroutines via
``asyncio.run`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import app.mcp.context as mcp_context
import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy import select

import app.api.v1.keywords as rest_keywords_module
from app.api.v1.keywords import (
    COMPETITOR_KEYWORD_CAP,
    add_competitor as rest_add_competitor,
    check_competitor_keywords as rest_check_competitor_keywords,
)
from app.core.ratelimit import rate_limit, reset_rate_limit_state
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.competitor import CompetitorApp
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordTracking
from app.models.personal_access_token import PersonalAccessToken
from app.models.territory import Territory
from app.models.user import User
from app.schemas.keyword import CompetitorCreate
from app.services.keyword_intel.asa_recommendations import _normalize_popularity
from app.services.keywords.tracker import (
    MAX_RANKING_CHECKS,
    KeywordRankingTracker,
)


# ---------------------------------------------------------------------------
# I4 — _normalize_popularity boundary bug
# ---------------------------------------------------------------------------


def test_normalize_popularity_boundary():
    # Dot scale (1-5) maps via x20, EXCEPT 5 which is the integer floor.
    assert _normalize_popularity(1) == 20
    assert _normalize_popularity(4) == 80
    # The ambiguous value: 5 must stay 5 (integer floor), NOT inflate to 100.
    assert _normalize_popularity(5) == 5
    # Integer scale (5-100) is used directly.
    assert _normalize_popularity(50) == 50
    assert _normalize_popularity(100) == 100
    # Out-of-range / missing.
    assert _normalize_popularity(0) is None
    assert _normalize_popularity(101) is None
    assert _normalize_popularity(None) is None


# ---------------------------------------------------------------------------
# I1 — per-user rate-limit dependency
# ---------------------------------------------------------------------------


def test_rate_limit_dependency_returns_429_after_budget():
    async def go() -> None:
        reset_rate_limit_state()
        dep = rate_limit("test.endpoint", per_min=3)
        user = {"user_id": "42"}
        other = {"user_id": "99"}

        # First 3 calls allowed for user 42.
        for _ in range(3):
            assert await dep(current_user=user) is user

        # 4th call for the same user is blocked with 429.
        with pytest.raises(HTTPException) as exc_info:
            await dep(current_user=user)
        assert exc_info.value.status_code == 429

        # A different user still has a fresh budget.
        assert await dep(current_user=other) is other

        # Resetting the bucket restores the original user's budget.
        reset_rate_limit_state()
        assert await dep(current_user=user) is user

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Shared world seeding for competitor / ranking tests
# ---------------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_app_with_keywords(n_keywords: int) -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        owner = User(
            email=f"kw-hard-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="KW Hardening Owner",
        )
        session.add(owner)
        await session.flush()

        pat = PersonalAccessToken(
            user_id=owner.id,
            name="kw-hard-pat",
            token_hash=f"kw-hard-hash-{suffix}",
        )
        session.add(pat)
        await session.flush()

        credential = ASCCredential(
            user_id=owner.id,
            name="KW Hardening ASC",
            issuer_id=f"kw-hard-issuer-{suffix}",
            key_id=f"kw-hard-key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(credential)
        await session.flush()

        app = App(
            credential_id=credential.id,
            asc_app_id="1111111111",
            bundle_id="ai.overx.kw-hard",
            name="KW Hardening App",
            platform="ios",
        )
        session.add(app)
        await session.flush()

        competitor = CompetitorApp(
            app_id=app.id,
            asc_app_id="2222222222",
            name="Rival",
            bundle_id="ai.overx.rival",
        )
        session.add(competitor)
        await session.flush()

        territory = (
            await session.execute(select(Territory).where(Territory.code == "US"))
        ).scalar_one_or_none()
        if territory is None:
            territory = Territory(code="US", name="United States", currency_code="USD")
            session.add(territory)
            await session.flush()

        for i in range(n_keywords):
            keyword = Keyword(text=f"kw-{suffix}-{i}", locale="en-US")
            session.add(keyword)
            await session.flush()
            session.add(KeywordTracking(app_id=app.id, keyword_id=keyword.id))
        await session.commit()

        return {
            "owner_user_id": owner.id,
            "app_id": app.id,
            "competitor_id": competitor.id,
        }


def _fake_access_token(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": "1"})


class _RecordingSearchService:
    """Stand-in for ITunesSearchService that records call volume and the peak
    in-flight concurrency, without touching the network."""

    def __init__(self) -> None:
        self.search_calls = 0
        self.batch_terms_seen = 0
        self.rank_calls = 0
        self.in_flight = 0
        self.peak_in_flight = 0

    async def _track(self) -> None:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        await asyncio.sleep(0)  # yield so concurrent tasks interleave
        self.in_flight -= 1

    async def search_apps(self, term, country="us", limit=200, *, client=None):
        self.search_calls += 1
        await self._track()
        return []

    async def search_apps_batch(self, terms, *, concurrency=5):
        # Mirror the real batch's bounded-concurrency semantics so the cap and
        # concurrency assertions are meaningful.
        self.batch_terms_seen = len(terms)
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(_term, _country):
            async with semaphore:
                self.search_calls += 1
                await self._track()
                return []

        return await asyncio.gather(*(_one(t, c) for t, c in terms))

    async def get_app_rank(self, term, app_id, country="us", *, client=None):
        self.rank_calls += 1
        await self._track()
        return None


# ---------------------------------------------------------------------------
# I2 — competitor-keyword fan-out is capped + bounded
# ---------------------------------------------------------------------------


def test_competitor_keyword_check_caps_fanout(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        # Seed more keywords than the cap to prove truncation.
        seeded = await _seed_app_with_keywords(COMPETITOR_KEYWORD_CAP + 10)

        recorder = _RecordingSearchService()
        # The matrix helper builds the service via the name bound in the REST
        # module, so patch it there.
        monkeypatch.setattr(
            rest_keywords_module, "ITunesSearchService", lambda: recorder,
        )

        async with async_session_factory() as session:
            results = await rest_check_competitor_keywords(
                seeded["app_id"],
                seeded["competitor_id"],
                {"user_id": seeded["owner_user_id"]},
                session,
            )

        # Cap honoured: at most COMPETITOR_KEYWORD_CAP keywords processed.
        assert len(results) == COMPETITOR_KEYWORD_CAP
        assert recorder.batch_terms_seen == COMPETITOR_KEYWORD_CAP
        assert recorder.search_calls == COMPETITOR_KEYWORD_CAP
        # Concurrency bounded (never more than the batch's default of 5).
        assert 1 <= recorder.peak_in_flight <= 5

    asyncio.run(go())


# ---------------------------------------------------------------------------
# I3 — ranking refresh caps work + bounded concurrency
# ---------------------------------------------------------------------------


def test_refresh_rankings_caps_checks(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        # One territory (US) per keyword, so seed > MAX_RANKING_CHECKS keywords.
        seeded = await _seed_app_with_keywords(MAX_RANKING_CHECKS + 5)

        async with async_session_factory() as session:
            tracker = KeywordRankingTracker(session)
            recorder = _RecordingSearchService()
            tracker.search_service = recorder

            recorded = await tracker.refresh_rankings(seeded["app_id"])

        # Hard cap on external rank checks.
        assert recorded == MAX_RANKING_CHECKS
        assert recorder.rank_calls == MAX_RANKING_CHECKS
        # Concurrency bounded (refresh uses a Semaphore of 5).
        assert 1 <= recorder.peak_in_flight <= 5

    asyncio.run(go())


# ---------------------------------------------------------------------------
# M5 — add_competitor rejects non-numeric asc_app_id
# ---------------------------------------------------------------------------


def test_rest_add_competitor_rejects_non_numeric_id():
    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_app_with_keywords(0)

        body = CompetitorCreate(asc_app_id="com.evil.bundle", name="Bad")
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await rest_add_competitor(
                    seeded["app_id"],
                    body,
                    {"user_id": seeded["owner_user_id"]},
                    session,
                )
        assert exc_info.value.status_code == 400

    asyncio.run(go())


def test_mcp_add_competitor_rejects_non_numeric_id(monkeypatch):
    from app.mcp.tools.keywords import add_competitor as mcp_add_competitor

    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_app_with_keywords(0)

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(seeded["owner_user_id"]),
        )

        with pytest.raises(ToolError, match="numeric iTunes track ID"):
            await mcp_add_competitor.fn(
                app_id=seeded["app_id"],
                asc_app_id="not-a-number",
                name="Bad",
            )

    asyncio.run(go())
