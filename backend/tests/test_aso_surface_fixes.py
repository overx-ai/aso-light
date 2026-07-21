"""Tests for the ASO-analysis + app/credential /code-pass fixes.

Covers:
  * I1 — the ``clash.run`` MCP tool rejects a non-2-letter ``country`` with a
    ``ToolError`` (mirrors the REST query-param validation).
  * I2 — ``WatchCreate`` rejects 3+ char and unknown 2-char country codes, and
    the REST/MCP create paths return 400 / ToolError for unknown storefronts.
  * I3 — the availability endpoint maps an ``ASCAPIError`` to a stable 502
    message instead of echoing Apple's raw text.
  * M4 — availability on an app with an empty ``asc_app_id`` returns 409.
  * M8 — ``list_credentials`` reports the real ``apps_count``.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import app.mcp.context as mcp_context
import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

import app.api.v1.availability as availability_module
from app.api.v1.availability import get_availability, update_availability
from app.api.v1.credentials import list_credentials
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.schemas.availability import AppAvailabilityUpdateRequest
from app.schemas.visibility import WatchCreate, is_known_storefront
from app.services.asc.errors import ASCAPIError


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _fake_access_token(user_id: int, pat_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": str(pat_id)})


async def _seed_user_credential_app(
    *, asc_app_id: str = "adam-123",
) -> dict[str, int]:
    """Seed one user → one credential → one app, returning their ids."""
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        user = User(
            email=f"surface-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Surface Owner",
        )
        session.add(user)
        await session.flush()

        credential = ASCCredential(
            user_id=user.id,
            name="ASC",
            issuer_id=f"iss-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(credential)
        await session.flush()

        app = App(
            credential_id=credential.id,
            asc_app_id=asc_app_id,
            bundle_id=f"com.example.surface.{suffix}",
            name="Surface App",
            platform="ios",
        )
        session.add(app)
        await session.commit()
        return {
            "user_id": user.id,
            "credential_id": credential.id,
            "app_id": app.id,
        }


class _DummyASCClient:
    """Minimal async-context-manager stand-in for ASCClient."""

    async def __aenter__(self) -> "_DummyASCClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


# ---------------------------------------------------------------------------
# I1 — clash MCP tool validates country length
# ---------------------------------------------------------------------------


def test_clash_tool_rejects_three_letter_country(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(seeded["user_id"]),
        )
        tool = await mcp.get_tool("clash_run")
        assert tool is not None
        with pytest.raises(ToolError, match="country must be a 2-letter code"):
            await tool.fn(app_id=seeded["app_id"], country="usa")

    asyncio.run(go())


# ---------------------------------------------------------------------------
# I2 — WatchCreate rejects long / unknown country codes
# ---------------------------------------------------------------------------


def test_watch_create_rejects_three_char_country():
    with pytest.raises(ValidationError):
        WatchCreate(text="coffee", country="usa")


def test_watch_create_accepts_known_two_letter_country():
    body = WatchCreate(text="coffee", country="US")
    assert body.country.strip().lower() == "us"


def test_is_known_storefront():
    assert is_known_storefront("us") is True
    assert is_known_storefront("US") is True
    assert is_known_storefront("zz") is False


def test_create_watch_rest_rejects_unknown_two_char_country():
    """A syntactically-valid but unknown 2-char storefront is a 400, not a
    silently-broken watch."""
    from app.api.v1.visibility import create_watch

    async def go() -> tuple[int, str]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        body = WatchCreate(text="coffee", country="zz")
        async with async_session_factory() as session:
            try:
                await create_watch(
                    app_id=seeded["app_id"],
                    body=body,
                    current_user={"user_id": str(seeded["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = asyncio.run(go())
    assert status_code == 400, (status_code, detail)
    assert "Unknown territory" in detail


def test_create_watch_mcp_rejects_unknown_two_char_country(monkeypatch):
    async def go() -> None:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        monkeypatch.setattr(
            mcp_context,
            "get_access_token",
            lambda: _fake_access_token(seeded["user_id"]),
        )
        tool = await mcp.get_tool("visibility_create_watch")
        assert tool is not None
        with pytest.raises(ToolError, match="Unknown territory"):
            await tool.fn(app_id=seeded["app_id"], text="coffee", country="zz")

    asyncio.run(go())


# ---------------------------------------------------------------------------
# I3 — availability maps ASCAPIError to a stable 502 message
# ---------------------------------------------------------------------------


def test_get_availability_maps_asc_error_to_stable_502(monkeypatch):
    async def go() -> tuple[int, str]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app(asc_app_id="adam-i3")

        async def fake_client(app, session):
            return _DummyASCClient()

        async def fake_get(self, asc_app_id):
            raise ASCAPIError(
                422,
                {"errors": [{"detail": "Apple internal raw detail XYZ"}]},
            )

        monkeypatch.setattr(
            availability_module, "_get_asc_client_for_app", fake_client,
        )
        monkeypatch.setattr(
            "app.services.asc.availability.ASCAvailabilityService."
            "get_app_availability",
            fake_get,
        )

        async with async_session_factory() as session:
            try:
                await get_availability(
                    app_id=seeded["app_id"],
                    current_user={"user_id": str(seeded["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = asyncio.run(go())
    assert status_code == 502, (status_code, detail)
    assert detail == "App Store Connect rejected the availability request."
    # The raw Apple text must not leak into the response.
    assert "XYZ" not in detail


# ---------------------------------------------------------------------------
# M4 — availability on a never-synced app (empty asc_app_id) returns 409
# ---------------------------------------------------------------------------


def test_get_availability_unsynced_app_returns_409():
    async def go() -> tuple[int, str]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app(asc_app_id="")
        async with async_session_factory() as session:
            try:
                await get_availability(
                    app_id=seeded["app_id"],
                    current_user={"user_id": str(seeded["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = asyncio.run(go())
    assert status_code == 409, (status_code, detail)
    assert "not yet synced" in detail


def test_update_availability_unsynced_app_returns_409():
    async def go() -> tuple[int, str]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app(asc_app_id="")
        body = AppAvailabilityUpdateRequest(disabled_territories=[])
        async with async_session_factory() as session:
            try:
                await update_availability(
                    app_id=seeded["app_id"],
                    body=body,
                    current_user={"user_id": str(seeded["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = asyncio.run(go())
    assert status_code == 409, (status_code, detail)
    assert "not yet synced" in detail


# ---------------------------------------------------------------------------
# M8 — list_credentials reports the real apps_count
# ---------------------------------------------------------------------------


def test_list_credentials_reports_real_apps_count():
    async def go() -> list[int]:
        await _ensure_schema()
        suffix = uuid.uuid4().hex[:8]
        n_apps = 3
        async with async_session_factory() as session:
            user = User(
                email=f"count-{suffix}@example.com",
                password_hash=hash_password("password-123"),
                name="Count Owner",
            )
            session.add(user)
            await session.flush()

            credential = ASCCredential(
                user_id=user.id,
                name="ASC",
                issuer_id=f"iss-{suffix}",
                key_id=f"key-{suffix}",
                private_key_encrypted=encrypt_value("fixture-private-key"),
            )
            session.add(credential)
            await session.flush()

            for i in range(n_apps):
                session.add(
                    App(
                        credential_id=credential.id,
                        asc_app_id=f"adam-{suffix}-{i}",
                        bundle_id=f"com.example.count.{suffix}.{i}",
                        name=f"Count App {i}",
                        platform="ios",
                    )
                )
            await session.commit()
            user_id, credential_id = user.id, credential.id

        async with async_session_factory() as session:
            responses = await list_credentials(
                current_user={"user_id": str(user_id)},
                session=session,
            )
            counts = [r.apps_count for r in responses if r.id == credential_id]
        return counts

    counts = asyncio.run(go())
    assert counts == [3], counts
