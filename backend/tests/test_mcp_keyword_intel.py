from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import app.mcp.context as mcp_context
import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.api.v1.keywords import (
    list_tracked_keywords as rest_list_tracked_keywords,
    refresh_keyword_rankings as rest_refresh_keyword_rankings,
)
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking
from app.models.personal_access_token import PersonalAccessToken
from app.models.territory import Territory
from app.models.user import User
from app.services.keywords.tracker import KeywordRankingTracker


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

        await session.commit()

        return {
            "owner_user_id": owner.id,
            "owner_pat_id": owner_pat.id,
            "owner_app_id": owner_app.id,
            "other_user_id": other_user.id,
            "other_pat_id": other_pat.id,
            "other_app_id": other_app.id,
            "keyword_text": keyword.text,
        }


def _fake_access_token(user_id: int, pat_id: int) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": str(pat_id)})


def test_keyword_intel_tools_are_registered():
    async def go() -> None:
        list_tool = await mcp.get_tool("keyword_intel_list_for_app")
        refresh_tool = await mcp.get_tool("keyword_intel_refresh")
        assert list_tool is not None
        assert refresh_tool is not None
        assert list_tool.name == "keyword_intel_list_for_app"
        assert refresh_tool.name == "keyword_intel_refresh"

    asyncio.run(go())


def test_keyword_intel_list_for_app_matches_rest_and_enforces_ownership(monkeypatch):
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

        tool = await mcp.get_tool("keyword_intel_list_for_app")
        assert tool is not None

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


def test_keyword_intel_refresh_matches_rest(monkeypatch):
    async def fake_refresh(
        self: KeywordRankingTracker,
        app_id: int,
        territory_codes: list[str] | None = None,
    ) -> int:
        assert territory_codes is None
        assert app_id > 0
        return 7

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
            KeywordRankingTracker,
            "refresh_rankings",
            fake_refresh,
        )

        tool = await mcp.get_tool("keyword_intel_refresh")
        assert tool is not None

        async with async_session_factory() as session:
            expected = await rest_refresh_keyword_rankings(
                seeded["owner_app_id"],
                {"user_id": seeded["owner_user_id"]},
                session,
            )

        actual = await tool.fn(app_id=seeded["owner_app_id"])
        assert actual == expected == {"recorded": 7}

    asyncio.run(go())
