import httpx
import pytest

from app.services.asc import client as client_module
from app.services.asc.client import _MAX_RETRIES, ASCClient
from app.services.asc.errors import ASCAPIError, ASCNetworkError


def _scripted(statuses: list[int], calls: list[httpx.Request], body: dict | None = None):
    """A transport answering ``statuses`` in order, then 200 forever."""
    remaining = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = remaining.pop(0) if remaining else 200
        if status >= 400:
            return httpx.Response(status, json={"errors": [{"detail": f"http {status}"}]})
        return httpx.Response(status, json=body if body is not None else {"data": []})

    return httpx.MockTransport(handler)


@pytest.fixture
def asc(monkeypatch):
    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(ASCClient, "_throttle", no_wait)

    def build(transport: httpx.MockTransport) -> ASCClient:
        client = ASCClient("issuer", "key", "unused")
        http = httpx.AsyncClient(transport=transport, base_url=ASCClient.BASE_URL)

        async def get_client():
            return http

        monkeypatch.setattr(client, "_get_client", get_client)
        return client

    return build


async def test_get_answered_500_then_200_returns_the_second_body(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([500], calls, body={"data": [{"id": "a"}]}))

    assert await client._get("/apps") == {"data": [{"id": "a"}]}
    assert len(calls) == 2


async def test_patch_answered_502_is_retried(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([502], calls))

    await client._patch("/appScreenshots/x", json={"data": {}})
    assert len(calls) == 2


async def test_delete_answered_504_is_retried(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([504], calls))

    await client._delete("/appScreenshots/x")
    assert len(calls) == 2


async def test_persistent_503_raises_the_last_api_error_after_every_retry(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([503] * _MAX_RETRIES, calls))

    with pytest.raises(ASCAPIError) as raised:
        await client._get("/apps")
    assert raised.value.status_code == 503
    assert len(calls) == _MAX_RETRIES


async def test_post_answered_500_raises_at_once(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([500], calls))

    with pytest.raises(ASCAPIError):
        await client._post("/appScreenshots", json={"data": {}})
    assert len(calls) == 1


async def test_429_is_still_retried(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([429], calls))

    await client._get("/apps")
    assert len(calls) == 2


async def test_paginated_get_whose_second_page_answers_502_once_returns_every_item(asc):
    calls: list[httpx.Request] = []
    pages = iter([
        httpx.Response(200, json={"data": [{"id": 1}], "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
        httpx.Response(502, json={"errors": []}),
        httpx.Response(200, json={"data": [{"id": 2}], "links": {}}),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(pages)

    client = asc(httpx.MockTransport(handler))

    assert await client._get_all_pages("/apps") == [{"id": 1}, {"id": 2}]
    assert len(calls) == 3


async def test_upload_put_answered_503_is_retried(asc, monkeypatch):
    calls: list[httpx.Request] = []
    transport = _scripted([503], calls)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    client = ASCClient("issuer", "key", "unused")

    await client._put_binary("https://upload.example/slot", b"png")
    assert len(calls) == 2


def _failing(errors: list[Exception | int], calls: list[httpx.Request]):
    """A transport raising or answering each entry in order, then 200 forever."""
    remaining = list(errors)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        entry = remaining.pop(0) if remaining else 200
        if isinstance(entry, Exception):
            raise entry
        if entry >= 400:
            return httpx.Response(entry, text="<html>Bad Gateway</html>")
        return httpx.Response(entry, json={"data": []})

    return httpx.MockTransport(handler)


async def test_persistent_html_5xx_raises_api_error_not_a_json_decode_error(asc):
    calls: list[httpx.Request] = []
    client = asc(_failing([502] * _MAX_RETRIES, calls))

    with pytest.raises(ASCAPIError) as raised:
        await client._get("/apps")
    assert raised.value.status_code == 502
    assert "Bad Gateway" in str(raised.value)


async def test_get_read_timeout_is_retried(asc):
    calls: list[httpx.Request] = []
    client = asc(_failing([httpx.ReadTimeout("slow")], calls))

    await client._get("/apps")
    assert len(calls) == 2


async def test_post_connect_error_is_retried_because_nothing_was_sent(asc):
    calls: list[httpx.Request] = []
    client = asc(_failing([httpx.ConnectError("refused")], calls))

    await client._post("/appScreenshots", json={"data": {}})
    assert len(calls) == 2


async def test_post_read_timeout_is_not_retried_and_raises_a_network_error(asc):
    calls: list[httpx.Request] = []
    client = asc(_failing([httpx.ReadTimeout("slow")], calls))

    with pytest.raises(ASCNetworkError, match="ReadTimeout"):
        await client._post("/appScreenshots", json={"data": {}})
    assert len(calls) == 1


async def test_persistent_network_error_raises_a_network_error_after_every_retry(asc):
    calls: list[httpx.Request] = []
    client = asc(_failing([httpx.ConnectError("down")] * _MAX_RETRIES, calls))

    with pytest.raises(ASCNetworkError):
        await client._get("/apps")
    assert len(calls) == _MAX_RETRIES


async def test_paginated_get_retries_a_read_timeout_on_a_later_page(asc):
    calls: list[httpx.Request] = []
    pages = iter([
        httpx.Response(200, json={"data": [{"id": 1}], "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json={"data": [{"id": 2}], "links": {}}),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        entry = next(pages)
        if isinstance(entry, Exception):
            raise entry
        return entry

    client = asc(httpx.MockTransport(handler))

    assert await client._get_all_pages("/apps") == [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize("failure", [429, httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
async def test_upload_put_retries_rate_limits_and_network_errors(asc, monkeypatch, failure):
    calls: list[httpx.Request] = []
    transport = _failing([failure], calls)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    client = ASCClient("issuer", "key", "unused")

    await client._put_binary("https://upload.example/slot", b"png")
    assert len(calls) == 2
