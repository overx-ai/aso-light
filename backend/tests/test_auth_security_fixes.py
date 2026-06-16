"""Tests for the auth-core security fix set.

Covers:
* C1/I4 — ``Settings`` fail-fast validators reject weak/placeholder secrets and
  empty/invalid Fernet keys, and accept strong ones.
* C2/I3 — ``get_current_user`` enforces existence + ``is_active`` and validates
  ``sub``; ``/refresh`` re-loads the user and rejects deactivated accounts.
* I1 — ``/login`` returns 429 after the per-IP budget from a single IP.
* I2 — register rejects an over-length (> 72 char) password.
* M4 — login with a non-existent email returns the generic 401 without 500.

Backend convention: keep the pytest entrypoint sync and drive coroutines via
``asyncio.run`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.api.v1.auth import login, refresh
from app.core.config import Settings
from app.core.ratelimit import ip_rate_limit, reset_rate_limit_state
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
)
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest

_STRONG_SECRET = "x" * 40
_VALID_FERNET = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# C1/I4 — Settings fail-fast validators
# ---------------------------------------------------------------------------


def _settings(**overrides: str) -> Settings:
    base = {
        "JWT_SECRET_KEY": _STRONG_SECRET,
        "SECRET_KEY": _STRONG_SECRET,
        "FERNET_KEY": _VALID_FERNET,
    }
    base.update(overrides)
    # ``_env_file=None`` ignores the dev .env so the test is hermetic.
    return Settings(_env_file=None, **base)


def test_settings_accepts_strong_secrets():
    settings = _settings()
    assert settings.JWT_SECRET_KEY == _STRONG_SECRET
    assert settings.SECRET_KEY == _STRONG_SECRET
    assert settings.FERNET_KEY == _VALID_FERNET


def test_settings_rejects_placeholder_jwt_secret():
    with pytest.raises(ValidationError):
        _settings(JWT_SECRET_KEY="change-me-jwt-secret")


def test_settings_rejects_placeholder_secret_key():
    with pytest.raises(ValidationError):
        _settings(SECRET_KEY="change-me-in-production")


def test_settings_rejects_empty_jwt_secret():
    with pytest.raises(ValidationError):
        _settings(JWT_SECRET_KEY="")


def test_settings_rejects_short_jwt_secret():
    with pytest.raises(ValidationError):
        _settings(JWT_SECRET_KEY="too-short")


def test_settings_rejects_short_secret_key():
    with pytest.raises(ValidationError):
        _settings(SECRET_KEY="too-short")


def test_settings_rejects_empty_fernet_key():
    with pytest.raises(ValidationError):
        _settings(FERNET_KEY="")


def test_settings_rejects_invalid_fernet_key():
    with pytest.raises(ValidationError):
        _settings(FERNET_KEY="not-a-valid-fernet-key")


# ---------------------------------------------------------------------------
# Shared DB helpers for C2/I3, I1, M4
# ---------------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_user(*, is_active: bool = True) -> int:
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        user = User(
            email=f"auth-sec-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Auth Sec",
            is_active=is_active,
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        await session.commit()
    return user_id


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# C2/I3 — get_current_user existence + active enforcement
# ---------------------------------------------------------------------------


def test_get_current_user_succeeds_for_active_user():
    async def go() -> None:
        await _ensure_schema()
        user_id = await _seed_user(is_active=True)
        token = create_access_token({"sub": str(user_id)})
        async with async_session_factory() as session:
            result = await get_current_user(_bearer(token), session=session)
        # Contract preserved: string-form subject under "user_id".
        assert result["user_id"] == str(user_id)
        assert result["type"] == "access"

    asyncio.run(go())


def test_get_current_user_rejects_deactivated_user():
    async def go() -> None:
        await _ensure_schema()
        user_id = await _seed_user(is_active=False)
        token = create_access_token({"sub": str(user_id)})
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(_bearer(token), session=session)
        assert exc_info.value.status_code == 401

    asyncio.run(go())


def test_get_current_user_rejects_nonexistent_user():
    async def go() -> None:
        await _ensure_schema()
        token = create_access_token({"sub": "99999999"})
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(_bearer(token), session=session)
        assert exc_info.value.status_code == 401

    asyncio.run(go())


def test_get_current_user_rejects_non_numeric_sub():
    async def go() -> None:
        await _ensure_schema()
        token = create_access_token({"sub": "not-a-number"})
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(_bearer(token), session=session)
        assert exc_info.value.status_code == 401

    asyncio.run(go())


def test_refresh_rejects_deactivated_user():
    async def go() -> None:
        await _ensure_schema()
        user_id = await _seed_user(is_active=False)
        token = create_refresh_token({"sub": str(user_id)})
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await refresh(SimpleNamespace(refresh_token=token), session=session)
        assert exc_info.value.status_code == 401

    asyncio.run(go())


def test_refresh_succeeds_for_active_user():
    async def go() -> None:
        await _ensure_schema()
        user_id = await _seed_user(is_active=True)
        token = create_refresh_token({"sub": str(user_id)})
        async with async_session_factory() as session:
            result = await refresh(
                SimpleNamespace(refresh_token=token), session=session
            )
        assert result.access_token
        assert result.refresh_token

    asyncio.run(go())


# ---------------------------------------------------------------------------
# I1 — IP-keyed login rate limit
# ---------------------------------------------------------------------------


def test_login_returns_429_after_ip_budget():
    async def go() -> None:
        reset_rate_limit_state()
        dep = ip_rate_limit("auth.login", per_min=3)
        request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))

        # First 3 calls from the IP are allowed.
        for _ in range(3):
            assert await dep(request) is None

        # 4th call from the same IP is blocked with 429.
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 429

        # A different IP still has a fresh budget.
        other = SimpleNamespace(client=SimpleNamespace(host="198.51.100.2"))
        assert await dep(other) is None

        reset_rate_limit_state()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# I2 — register rejects an over-length password (bcrypt 72-byte truncation)
# ---------------------------------------------------------------------------


def test_register_rejects_over_length_password():
    with pytest.raises(ValidationError):
        RegisterRequest(email="x@example.com", password="a" * 73, name="X")


def test_register_accepts_max_length_password():
    body = RegisterRequest(email="x@example.com", password="a" * 72, name="X")
    assert len(body.password) == 72


# ---------------------------------------------------------------------------
# M4 — login no-user path returns generic 401 (and does not 500)
# ---------------------------------------------------------------------------


def test_login_unknown_email_returns_generic_401():
    async def go() -> None:
        await _ensure_schema()
        reset_rate_limit_state()
        body = LoginRequest(
            email=f"missing-{uuid.uuid4().hex}@example.com",
            password="whatever-password",
        )
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await login(body, session=session)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid email or password"

    asyncio.run(go())
