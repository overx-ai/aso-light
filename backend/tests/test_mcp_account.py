from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import app.mcp.context as mcp_context
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_world() -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8]

    async with async_session_factory() as session:
        owner = User(
            email=f"owner-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Owner",
        )
        session.add(owner)
        await session.flush()

        owner_pat = PersonalAccessToken(
            user_id=owner.id,
            name="owner-pat",
            token_hash=f"hash-owner-{suffix}",
        )
        session.add(owner_pat)
        await session.flush()

        owner_credential = ASCCredential(
            user_id=owner.id,
            name="Primary ASC",
            issuer_id=f"issuer-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(owner_credential)
        await session.flush()

        session.add_all([
            App(
                credential_id=owner_credential.id,
                asc_app_id=f"adam-refresher-{suffix}",
                bundle_id="ai.overx.refresher",
                name="Refresher",
                platform="ios",
            ),
            App(
                credential_id=owner_credential.id,
                asc_app_id=f"adam-mushtra-{suffix}",
                bundle_id="ai.overx.mushtra",
                name="Mushtra",
                platform="ios",
            ),
        ])

        other_user = User(
            email=f"other-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Other",
        )
        session.add(other_user)
        await session.flush()

        other_pat = PersonalAccessToken(
            user_id=other_user.id,
            name="other-pat",
            token_hash=f"hash-other-{suffix}",
        )
        session.add(other_pat)
        await session.flush()

        other_credential = ASCCredential(
            user_id=other_user.id,
            name="Fixture ASC",
            issuer_id=f"issuer-other-{suffix}",
            key_id=f"key-other-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(other_credential)
        await session.flush()

        session.add(
            App(
                credential_id=other_credential.id,
                asc_app_id=f"adam-lunar-{suffix}",
                bundle_id="com.overmind.LunarCalendar",
                name="LunarCalendar",
                platform="ios",
            )
        )
        await session.commit()

        return {
            "owner_user_id": owner.id,
            "owner_pat_id": owner_pat.id,
            "owner_credential_id": owner_credential.id,
        }


def _fake_access_token(user_id: int, pat_id: int) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": str(pat_id)})


def test_account_whoami_tool_is_registered():
    async def go() -> str | None:
        tool = await mcp.get_tool("account_whoami")
        return None if tool is None else tool.name

    name = asyncio.run(go())
    assert name == "account_whoami"


def test_account_whoami_returns_pat_user_context(monkeypatch):
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

        tool = await mcp.get_tool("account_whoami")
        assert tool is not None
        result = await tool.fn()

        assert result.user.id == seeded["owner_user_id"]
        assert result.personal_access_token.id == seeded["owner_pat_id"]
        assert result.credential_count == 1
        assert result.app_count == 2
        assert [cred.id for cred in result.asc_credentials] == [
            seeded["owner_credential_id"]
        ]
        assert [cred.apps_count for cred in result.asc_credentials] == [2]
        assert {app.name for app in result.apps} == {"Refresher", "Mushtra"}
        assert all(app.bundle_id != "com.overmind.LunarCalendar" for app in result.apps)

    asyncio.run(go())
