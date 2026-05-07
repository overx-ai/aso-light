import asyncio

import httpx
import pytest

from app.services.asa.client import ASAClient, ASA_API_BASE
from app.services.asa.errors import ASAAPIError


def _build_client(handler) -> ASAClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=ASA_API_BASE)
    return ASAClient.__test_with_token__(http=http, access_token="tok-abc")


def test_request_includes_bearer_and_org_context():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.setdefault("calls", []).append({
            "auth": req.headers.get("authorization"),
            "ctx": req.headers.get("x-ap-context"),
            "method": req.method,
            "url": str(req.url),
        })
        return httpx.Response(200, json={"data": []})

    async def go() -> dict:
        client = _build_client(handler)
        await client.request("GET", "/me/acl")
        await client.request("POST", "/campaigns/find", org_id=42, json={"q": 1})
        await client.aclose()
        return captured

    out = asyncio.run(go())
    assert out["calls"][0]["auth"] == "Bearer tok-abc"
    assert out["calls"][0]["ctx"] is None
    assert out["calls"][1]["ctx"] == "orgId=42"


def test_request_raises_on_4xx_with_status_and_body():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    async def go() -> ASAAPIError:
        client = _build_client(handler)
        try:
            await client.request("GET", "/anything")
        finally:
            await client.aclose()

    with pytest.raises(ASAAPIError) as ei:
        asyncio.run(go())
    assert ei.value.status == 403
    assert "forbidden" in (ei.value.body or "")


def test_get_all_pages_handles_pagination():
    pages = [
        {"data": [{"id": i} for i in range(1000)],
         "pagination": {"totalResults": 1500}},
        {"data": [{"id": i} for i in range(1000, 1500)],
         "pagination": {"totalResults": 1500}},
    ]
    state = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        page = pages[state["i"]]
        state["i"] += 1
        return httpx.Response(200, json=page)

    async def go() -> list:
        client = _build_client(handler)
        rows = await client.get_all_pages(
            "POST", "/campaigns/find", org_id=1,
        )
        await client.aclose()
        return rows

    rows = asyncio.run(go())
    assert len(rows) == 1500
    assert rows[0]["id"] == 0
    assert rows[-1]["id"] == 1499
