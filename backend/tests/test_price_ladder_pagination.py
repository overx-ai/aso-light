"""A price ladder is either complete or the fetch raises (docs/bugs/011).

A rate-limited later page used to end pagination quietly, so page 1 was
cached as the whole ladder and every price above its 200th tier resolved to
the highest one visible.
"""

import httpx
import pytest

from app.services.asc import client as client_module
from app.services.asc.client import ASCClient
from app.services.asc.errors import ASCAPIError
from app.services.asc.pricing import ASCPricingService

NEXT = "https://api.appstoreconnect.apple.com/v2/page?cursor=2"
TERRITORY = {"type": "territories", "id": "IRL", "attributes": {"currency": "EUR"}}


def _page(ids: list[int], next_url: str | None) -> httpx.Response:
    return httpx.Response(200, json={
        "data": [
            {"id": f"pp-{i}", "attributes": {"customerPrice": str(i), "proceeds": "1"},
             "relationships": {"territory": {"data": {"id": "IRL"}}}}
            for i in ids
        ],
        "included": [TERRITORY],
        "links": {"next": next_url} if next_url else {},
    })


def _service(monkeypatch, responses: list[httpx.Response]) -> ASCPricingService:
    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(ASCClient, "_throttle", no_wait)
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return remaining.pop(0) if remaining else httpx.Response(
            429, json={"errors": [{"detail": "rate limited"}]},
        )

    client = ASCClient("issuer", "key", "unused")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=ASCClient.BASE_URL,
    )

    async def get_client():
        return http

    monkeypatch.setattr(client, "_get_client", get_client)
    return ASCPricingService(client)


async def _ladder(service: ASCPricingService, kind: str) -> list[dict]:
    if kind == "iap":
        return await service.get_iap_price_points("iap-1", territory_code="IRL")
    return await service.get_price_points("sub-1", territory_code="IRL")


@pytest.mark.parametrize("kind", ["iap", "subscription"])
async def test_a_rate_limited_second_page_is_retried_not_dropped(monkeypatch, kind):
    service = _service(monkeypatch, [
        _page([1, 2], NEXT),
        httpx.Response(429, json={"errors": [{"detail": "rate limited"}]}),
        _page([3], None),
    ])

    ladder = await _ladder(service, kind)

    assert [p["price_point_id"] for p in ladder] == ["pp-1", "pp-2", "pp-3"]
    assert {p["currency_code"] for p in ladder} == {"EUR"}


@pytest.mark.parametrize("kind", ["iap", "subscription"])
async def test_a_second_page_that_keeps_failing_raises_instead_of_truncating(monkeypatch, kind):
    service = _service(monkeypatch, [_page([1, 2], NEXT)])

    with pytest.raises(ASCAPIError):
        await _ladder(service, kind)
