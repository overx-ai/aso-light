from importlib import import_module
from uuid import uuid4

from fastapi.testclient import TestClient


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

        protected = client.get(
            "/mcp/",
            headers={"Authorization": f"Bearer {pat_token}"},
        )
        assert protected.status_code != 401
