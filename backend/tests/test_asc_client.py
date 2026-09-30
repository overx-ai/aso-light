import httpx
import pytest

from app.services.asc import client as client_module
from app.services.asc.client import _MAX_RETRIES, ASCClient
from app.services.asc.errors import ASCAPIError, ASCNetworkError, ASCRateLimitError


def _replay(
    entries: list[httpx.Response | Exception],
    calls: list[httpx.Request],
    body: dict | None = None,
):
    """A transport answering or raising each entry in order, then 200 with ``body`` forever."""
    remaining = list(entries)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if not remaining:
            return httpx.Response(200, json=body if body is not None else {"data": []})
        entry = remaining.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry

    return httpx.MockTransport(handler)


def _scripted(statuses: list[int], calls: list[httpx.Request], body: dict | None = None):
    """``statuses`` answered with a JSON error body, then 200 forever."""
    errors = [
        httpx.Response(status, json={"errors": [{"detail": f"http {status}"}]})
        for status in statuses
    ]
    return _replay(errors, calls, body)


def _failing(entries: list[Exception | int], calls: list[httpx.Request]):
    """Each exception raised and each status answered with an HTML body, then 200 forever."""
    return _replay(
        [
            entry if isinstance(entry, Exception)
            else httpx.Response(entry, text="<html>Bad Gateway</html>")
            for entry in entries
        ],
        calls,
    )


def _route_uploads(monkeypatch, transport: httpx.MockTransport) -> None:
    """Every unauthenticated client the upload path opens goes through ``transport``."""
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )


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
    client = asc(_replay([
        httpx.Response(200, json={"data": [{"id": 1}], "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
        httpx.Response(502, json={"errors": []}),
        httpx.Response(200, json={"data": [{"id": 2}], "links": {}}),
    ], calls))

    assert await client._get_all_pages("/apps") == [{"id": 1}, {"id": 2}]
    assert len(calls) == 3


async def test_upload_put_answered_503_is_retried(asc, monkeypatch):
    calls: list[httpx.Request] = []
    _route_uploads(monkeypatch, _scripted([503], calls))
    client = ASCClient("issuer", "key", "unused")

    await client._put_binary("https://upload.example/slot", b"png")
    assert len(calls) == 2


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
    client = asc(_replay([
        httpx.Response(200, json={"data": [{"id": 1}], "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json={"data": [{"id": 2}], "links": {}}),
    ], calls))

    assert await client._get_all_pages("/apps") == [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize("failure", [429, httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
async def test_upload_put_retries_rate_limits_and_network_errors(asc, monkeypatch, failure):
    calls: list[httpx.Request] = []
    _route_uploads(monkeypatch, _failing([failure], calls))
    client = ASCClient("issuer", "key", "unused")

    await client._put_binary("https://upload.example/slot", b"png")
    assert len(calls) == 2


async def test_a_network_error_carries_a_valid_http_status(asc):
    client = asc(_failing([httpx.ReadTimeout("slow")], []))

    with pytest.raises(ASCNetworkError) as raised:
        await client._post("/appScreenshots", json={"data": {}})
    assert raised.value.status_code == 504
    assert "network error" in raised.value.message


@pytest.mark.parametrize("call", ["put", "get"])
async def test_binary_calls_exhausting_429_raise_a_rate_limit_error(asc, monkeypatch, call):
    _route_uploads(monkeypatch, _failing([429] * _MAX_RETRIES, []))
    client = ASCClient("issuer", "key", "unused")

    with pytest.raises(ASCRateLimitError):
        if call == "put":
            await client._put_binary("https://upload.example/slot", b"png")
        else:
            await client._get_binary("https://download.example/segment")


async def test_a_401_late_in_a_retry_chain_still_refreshes_the_token(asc, monkeypatch):
    calls: list[httpx.Request] = []
    client = asc(_scripted([503, 503, 401], calls))
    closes: list[None] = []

    async def close():
        closes.append(None)

    monkeypatch.setattr(client, "close", close)

    await client._get("/apps")
    assert (len(calls), len(closes)) == (4, 1)


async def test_a_second_401_raises_after_one_refresh(asc):
    calls: list[httpx.Request] = []
    client = asc(_scripted([401, 401], calls))

    with pytest.raises(ASCAPIError) as raised:
        await client._get("/apps")
    assert raised.value.status_code == 401
    assert len(calls) == 2


async def test_a_401_on_the_last_attempt_raises_it(asc):
    client = asc(_scripted([503] * (_MAX_RETRIES - 1) + [401], []))

    with pytest.raises(ASCAPIError) as raised:
        await client._get("/apps")
    assert raised.value.status_code == 401


async def test_paginated_get_refreshes_the_token_on_a_later_page(asc):
    calls: list[httpx.Request] = []
    client = asc(_replay([
        httpx.Response(200, json={"data": [{"id": 1}], "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
        httpx.Response(401, json={"errors": []}),
        httpx.Response(200, json={"data": [{"id": 2}], "links": {}}),
    ], calls))

    assert await client._get_all_pages("/apps") == [{"id": 1}, {"id": 2}]


async def test_a_retry_after_http_date_falls_back_to_the_exponential_delay(asc):
    calls: list[httpx.Request] = []
    client = asc(_replay(
        [httpx.Response(429, headers={"Retry-After": "Wed, 30 Sep 2026 07:28:00 GMT"})],
        calls,
    ))

    await client._get("/apps")
    assert len(calls) == 2


async def test_a_json_error_body_that_is_not_an_object_raises_an_api_error(asc):
    client = asc(_replay([httpx.Response(400, json=["bad"])], []))

    with pytest.raises(ASCAPIError) as raised:
        await client._get("/apps")
    assert raised.value.status_code == 400
