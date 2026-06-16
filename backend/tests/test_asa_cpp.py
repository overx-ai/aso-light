"""Unit tests for the ASA Custom Product Page ad service.

Exercises :mod:`app.services.asa.cpp_ads` against a mocked ASA client
(``httpx.MockTransport`` + the ``__test_with_token__`` factory, mirroring
``tests/test_asa_client.py``). No network, no DB — these assert the
endpoint paths, ``X-AP-Context`` org scoping, and request bodies sent to
Apple for the assign / unassign / list flows.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.asa import cpp_ads
from app.services.asa.client import ASA_API_BASE, ASAClient
from app.services.asa.errors import ASAAPIError


def _build_client(handler) -> ASAClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=ASA_API_BASE)
    return ASAClient.__test_with_token__(http=http, access_token="tok-cpp")


def test_assign_cpp_posts_ad_referencing_cpp():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured["method"] = req.method
        captured["url"] = str(req.url)
        captured["ctx"] = req.headers.get("x-ap-context")
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"data": {"id": 9001, "name": "cpp-ad"}})

    async def go() -> dict:
        client = _build_client(handler)
        try:
            return await cpp_ads.assign_cpp(
                client,
                org_id=42,
                campaign_id=111,
                adgroup_id=222,
                cpp_id="abc-cpp-id",
                name="cpp-ad",
            )
        finally:
            await client.aclose()

    result = asyncio.run(go())

    assert result == {"id": 9001, "name": "cpp-ad"}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/campaigns/111/adgroups/222/ads")
    assert captured["ctx"] == "orgId=42"
    # The CPP id is referenced on the Ad's creative.
    assert captured["body"]["creativeId"] == "abc-cpp-id"
    assert captured["body"]["productPageId"] == "abc-cpp-id"
    assert captured["body"]["name"] == "cpp-ad"


def test_unassign_cpp_deletes_ad():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["url"] = str(req.url)
        captured["ctx"] = req.headers.get("x-ap-context")
        return httpx.Response(204)

    async def go() -> None:
        client = _build_client(handler)
        try:
            await cpp_ads.unassign_cpp(
                client,
                org_id=7,
                campaign_id=111,
                adgroup_id=222,
                ad_id=9001,
            )
        finally:
            await client.aclose()

    asyncio.run(go())

    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/campaigns/111/adgroups/222/ads/9001")
    assert captured["ctx"] == "orgId=7"


def test_list_ads_uses_find_pagination():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(
            200,
            json={
                "data": [{"id": 1, "creativeId": "cpp-1"}],
                "pagination": {"totalResults": 1},
            },
        )

    async def go() -> list:
        client = _build_client(handler)
        try:
            return await cpp_ads.list_ads(
                client, org_id=5, campaign_id=111, adgroup_id=222,
            )
        finally:
            await client.aclose()

    rows = asyncio.run(go())

    assert rows == [{"id": 1, "creativeId": "cpp-1"}]
    assert calls[0].endswith("/campaigns/111/adgroups/222/ads/find")


def test_assign_cpp_raises_on_api_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad creative")

    async def go() -> None:
        client = _build_client(handler)
        try:
            await cpp_ads.assign_cpp(
                client,
                org_id=1,
                campaign_id=1,
                adgroup_id=1,
                cpp_id="x",
                name="n",
            )
        finally:
            await client.aclose()

    with pytest.raises(ASAAPIError) as ei:
        asyncio.run(go())
    assert ei.value.status == 400
