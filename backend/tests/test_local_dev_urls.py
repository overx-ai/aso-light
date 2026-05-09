from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.main import app
from app.models.user import User


def _register_and_login(client: TestClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    email = f"local-dev-{suffix}@example.com"
    password = "password-123"

    register_res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "name": "Local Dev User",
        },
    )
    assert register_res.status_code == 201

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    return email, login_res.json()["access_token"]


def _deactivate_user(email: str) -> None:
    async def go() -> None:
        async with async_session_factory() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()
            user.is_active = False
            await session.commit()

    asyncio.run(go())


def test_local_dev_login_health_and_mcp_urls_work():
    with TestClient(app) as client:
        health_res = client.get("/health")
        assert health_res.status_code == 200
        assert health_res.json() == {"status": "ok"}

        _, access_token = _register_and_login(client)

        pat_res = client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": "  claude-desktop  "},
        )
        assert pat_res.status_code == 201
        pat_payload = pat_res.json()
        assert pat_payload["name"] == "claude-desktop"

        redirect_res = client.get(
            "/mcp",
            headers={"Authorization": f"Bearer {pat_payload['token']}"},
            follow_redirects=False,
        )
        assert redirect_res.status_code == 307
        assert redirect_res.headers["location"] == "/mcp/"

        mcp_res = client.get(
            "/mcp/",
            headers={"Authorization": f"Bearer {pat_payload['token']}"},
        )
        assert mcp_res.status_code == 200


def test_pat_creation_rejects_blank_names_after_trimming():
    with TestClient(app) as client:
        _, access_token = _register_and_login(client)
        pat_res = client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": "   "},
        )
        assert pat_res.status_code == 422


def test_deactivated_user_pat_cannot_access_mcp():
    with TestClient(app) as client:
        email, access_token = _register_and_login(client)

        pat_res = client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": "automation"},
        )
        assert pat_res.status_code == 201
        token = pat_res.json()["token"]

        _deactivate_user(email)

        mcp_res = client.get(
            "/mcp/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert mcp_res.status_code == 401
