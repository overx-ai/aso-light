from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import app.mcp.context as mcp_context
import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select

import app.services.keyword_intel.service as intel_service
from app.api.v1.keyword_intel import list_keyword_intel as rest_list_keyword_intel
from app.api.v1.keywords import (
    list_tracked_keywords as rest_list_tracked_keywords,
)
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.keyword_intel import KeywordIntelCache
from app.models.personal_access_token import PersonalAccessToken
from app.models.territory import Territory
from app.models.user import User
from app.services.keyword_intel.base import KeywordIntel, KeywordIntelProvider


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_world() -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        owner = User(
            email=f"keyword-owner-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Keyword Owner",
        )
        session.add(owner)
        await session.flush()

        owner_pat = PersonalAccessToken(
            user_id=owner.id,
            name="keyword-owner-pat",
            token_hash=f"keyword-owner-hash-{suffix}",
        )
        session.add(owner_pat)
        await session.flush()

        owner_credential = ASCCredential(
            user_id=owner.id,
            name="Owner ASC",
            issuer_id=f"owner-issuer-{suffix}",
            key_id=f"owner-key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(owner_credential)
        await session.flush()

        owner_app = App(
            credential_id=owner_credential.id,
            asc_app_id=f"adam-owner-{suffix}",
            bundle_id="ai.overx.keyword-owner",
            name="Keyword Owner App",
            platform="ios",
        )
        session.add(owner_app)
        await session.flush()

        other_user = User(
            email=f"keyword-other-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Keyword Other",
        )
        session.add(other_user)
        await session.flush()

        other_pat = PersonalAccessToken(
            user_id=other_user.id,
            name="keyword-other-pat",
            token_hash=f"keyword-other-hash-{suffix}",
        )
        session.add(other_pat)
        await session.flush()

        other_credential = ASCCredential(
            user_id=other_user.id,
            name="Other ASC",
            issuer_id=f"other-issuer-{suffix}",
            key_id=f"other-key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(other_credential)
        await session.flush()

        other_app = App(
            credential_id=other_credential.id,
            asc_app_id=f"adam-other-{suffix}",
            bundle_id="ai.overx.keyword-other",
            name="Keyword Other App",
            platform="ios",
        )
        session.add(other_app)
        await session.flush()

        territory = (
            await session.execute(
                select(Territory).where(Territory.code == "US")
            )
        ).scalar_one_or_none()
        if territory is None:
            territory = Territory(
                code="US",
                name="United States",
                currency_code="USD",
            )
            session.add(territory)
            await session.flush()

        keyword = Keyword(text=f"meditation-{suffix}", locale="en-US")
        session.add(keyword)
        await session.flush()

        tracking = KeywordTracking(app_id=owner_app.id, keyword_id=keyword.id)
        session.add(tracking)
        await session.flush()

        session.add_all([
            KeywordRanking(
                tracking_id=tracking.id,
                territory_id=territory.id,
                rank=11,
                recorded_at=now - timedelta(days=1),
            ),
            KeywordRanking(
                tracking_id=tracking.id,
                territory_id=territory.id,
                rank=5,
                recorded_at=now,
            ),
        ])

        session.add_all([
            KeywordIntelCache(
                app_id=owner_app.id,
                keyword=keyword.text,
                locale="US",
                source="asa_search_terms",
                volume_score=61,
                difficulty_score=42,
                raw_score=1234,
                extra={"impressions": 1234},
                fetched_at=now,
            ),
            KeywordIntelCache(
                app_id=owner_app.id,
                keyword=f"calm-{suffix}",
                locale="DE",
                source="asa_recommendations",
                volume_score=30,
                difficulty_score=None,
                raw_score=30,
                extra=None,
                fetched_at=now - timedelta(days=2),
            ),
            # Belongs to the other tenant — must never appear in owner reads.
            KeywordIntelCache(
                app_id=other_app.id,
                keyword=f"leak-{suffix}",
                locale="US",
                source="asa_search_terms",
                volume_score=99,
                fetched_at=now,
            ),
        ])

        await session.commit()

        return {
            "owner_user_id": owner.id,
            "owner_pat_id": owner_pat.id,
            "owner_app_id": owner_app.id,
            "other_user_id": other_user.id,
            "other_pat_id": other_pat.id,
            "other_app_id": other_app.id,
            "keyword_text": keyword.text,
            "suffix": suffix,
        }


def _fake_access_token(user_id: int, pat_id: int) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": str(pat_id)})


class _FakeProvider(KeywordIntelProvider):
    """Deterministic stand-in for the ASA-backed providers."""

    name = "fake_provider"

    async def fetch(self, *, app_id, session, **kwargs):
        assert kwargs.get("days") == 14
        return [
            KeywordIntel(
                keyword="fake-term",
                locale="US",
                source=self.name,
                volume_score=77,
                difficulty_score=12,
                raw_score=77,
            ),
        ]


def test_keyword_intel_tools_are_registered():
    async def go() -> None:
        list_tool = await mcp.get_tool("keyword_intel_list")
        refresh_tool = await mcp.get_tool("keyword_intel_refresh_providers")
        assert list_tool.name == "keyword_intel_list"
        assert refresh_tool.name == "keyword_intel_refresh_providers"

        # Both mislabeled aliases are gone, and neither was replaced: the old
        # refresh duplicated keywords_refresh_rankings, the old list
        # duplicated keywords_list_for_app.
        getter = getattr(mcp, "list_tools", None) or mcp._list_tools
        names = {tool.name for tool in await getter()}
        assert "keyword_intel_refresh" not in names
        assert "keyword_intel_list_for_app" not in names
        assert "keywords_list_tracked" not in names
        assert "keywords_refresh_rankings" in names
        assert "keywords_list_for_app" in names

    asyncio.run(go())


def test_keyword_intel_list_returns_cache_rows_and_enforces_ownership(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_world()

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["owner_user_id"],
                seeded["owner_pat_id"],
            ),
        )

        tool = await mcp.get_tool("keyword_intel_list")

        async with async_session_factory() as session:
            expected = await rest_list_keyword_intel(
                seeded["owner_app_id"],
                None,
                None,
                None,
                200,
                {"user_id": seeded["owner_user_id"]},
                session,
            )

        actual = await tool.fn(app_id=seeded["owner_app_id"])
        assert [row.model_dump() for row in actual] == [
            row.model_dump() for row in expected
        ]
        # Newest first, and the other tenant's row is not visible.
        assert [r.keyword for r in actual] == [
            seeded["keyword_text"],
            f"calm-{seeded['suffix']}",
        ]
        assert actual[0].source == "asa_search_terms"
        assert actual[0].volume_score == 61
        assert actual[0].difficulty_score == 42

        # Filters mirror the REST query params.
        filtered = await tool.fn(
            app_id=seeded["owner_app_id"], locale="DE",
        )
        assert [r.keyword for r in filtered] == [f"calm-{seeded['suffix']}"]

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["other_user_id"],
                seeded["other_pat_id"],
            ),
        )

        with pytest.raises(ToolError, match="Not authorized to access this app"):
            await tool.fn(app_id=seeded["owner_app_id"])

    asyncio.run(go())


def test_keyword_intel_refresh_providers_upserts_and_enforces_ownership(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_world()

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["owner_user_id"],
                seeded["owner_pat_id"],
            ),
        )
        monkeypatch.setattr(
            intel_service, "PROVIDER_FACTORIES", (_FakeProvider,),
        )

        tool = await mcp.get_tool("keyword_intel_refresh_providers")

        result = await tool.fn(app_id=seeded["owner_app_id"], days=14)
        assert result.written_total == 1
        assert result.by_source == {"fake_provider": 1}
        assert result.skipped_sources == {}

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(KeywordIntelCache).where(
                        KeywordIntelCache.app_id == seeded["owner_app_id"],
                        KeywordIntelCache.source == "fake_provider",
                    )
                )
            ).scalars().all()
        assert [(r.keyword, r.volume_score) for r in rows] == [("fake-term", 77)]

        # Selecting by name works; an unknown name is a clean ToolError.
        named = await tool.fn(
            app_id=seeded["owner_app_id"], provider="fake_provider", days=14,
        )
        assert named.by_source == {"fake_provider": 1}
        with pytest.raises(ToolError, match="Unknown provider"):
            await tool.fn(app_id=seeded["owner_app_id"], provider="nope")

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["other_user_id"],
                seeded["other_pat_id"],
            ),
        )

        with pytest.raises(ToolError, match="Not authorized to access this app"):
            await tool.fn(app_id=seeded["owner_app_id"], days=14)

    asyncio.run(go())


def test_keywords_list_for_app_matches_rest_and_enforces_ownership(monkeypatch):
    """The surviving tracked-keywords tool — the intel aliases pointed here.

    Kept when ``keyword_intel_list_for_app`` was deleted rather than renamed,
    so the coverage that alias carried doesn't disappear with it.
    """

    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_world()

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["owner_user_id"],
                seeded["owner_pat_id"],
            ),
        )

        tool = await mcp.get_tool("keywords_list_for_app")

        async with async_session_factory() as session:
            expected = await rest_list_tracked_keywords(
                seeded["owner_app_id"],
                {"user_id": seeded["owner_user_id"]},
                session,
            )

        actual = await tool.fn(app_id=seeded["owner_app_id"])
        assert [row.model_dump() for row in actual] == [
            row.model_dump() for row in expected
        ]
        assert len(actual) == 1
        assert actual[0].keyword.text == seeded["keyword_text"]
        assert actual[0].latest_rank == 5
        assert actual[0].rank_change == 6

        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(
                seeded["other_user_id"],
                seeded["other_pat_id"],
            ),
        )

        with pytest.raises(ToolError, match="Not authorized to access this app"):
            await tool.fn(app_id=seeded["owner_app_id"])

    asyncio.run(go())
