import asyncio
from importlib import import_module
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select


async def _set_user_active(email: str, *, is_active: bool) -> None:
    from app.db.session import async_session_factory
    from app.models.user import User

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.is_active = is_active
        await session.commit()


def test_local_dev_health_login_and_mcp_flow():
    app_module = import_module("app.main")

    email = f"dev-local-{uuid4().hex}@example.com"
    password = "dev-password-123"

    with TestClient(app_module.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        register = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "name": "Dev Local",
            },
        )
        assert register.status_code == 201

        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200
        access_token = login.json()["access_token"]

        pat_create = client.post(
            "/api/v1/auth/tokens",
            json={"name": "Local MCP"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert pat_create.status_code == 201
        pat_token = pat_create.json()["token"]

        redirect = client.get(
            "/mcp",
            follow_redirects=False,
            headers={"Authorization": f"Bearer {pat_token}"},
        )
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "/mcp/"

        mcp_headers = {
            "Authorization": f"Bearer {pat_token}",
            "Accept": "text/event-stream",
        }
        protected = client.get(
            "/mcp/",
            headers=mcp_headers,
        )
        assert protected.status_code == 400
        assert "Missing session ID" in protected.text

        asyncio.run(_set_user_active(email, is_active=False))

        deactivated = client.get(
            "/mcp/",
            headers=mcp_headers,
        )
        assert deactivated.status_code == 401
