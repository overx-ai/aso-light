"""A subscription price increase can spare existing subscribers.

Apple's ``preserveCurrentPrice`` keeps current subscribers on their old
price. Both apply paths sent ``False`` unconditionally, so every increase
reached every subscriber. ``preserve_current_price_on_increase`` sets it
per territory, only where the price goes up: a decrease must still reach
everyone, or they keep paying the higher price.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import app.models  # noqa: F401,E402

from _async_harness import run_async  # noqa: E402
from test_price_versions import (  # noqa: E402
    _by_code,
    _cache_sub_price,
    _seed_subscription,
    _versions,
)
from test_pricing_fixes import _patch_asc, _seed_iap_fixture  # noqa: E402

# The stubbed ladder offers one tier, "pp-x" at 4.99, in every territory.
CURRENT = {"US": 3.99, "DE": 5.99}
ITEMS = [{"territory_code": code, "price_point_id": "pp-x"} for code in CURRENT]


async def _apply_rest(app_id, sub_id, user_id, request):
    from app.api.v1.pricing import apply_subscription_prices
    from app.db.session import async_session_factory
    from app.schemas.pricing import PriceApplyRequest

    async with async_session_factory() as session:
        return await apply_subscription_prices(
            app_id=app_id, subscription_id=sub_id,
            body=PriceApplyRequest.model_validate(request),
            current_user={"user_id": str(user_id)}, session=session,
        )


async def _apply_mcp(app_id, sub_id, user_id, request, monkeypatch):
    from app.mcp import context as mcp_context
    from app.mcp.server import mcp

    monkeypatch.setattr(
        mcp_context, "get_access_token",
        lambda: SimpleNamespace(claims={"user_id": str(user_id)}),
    )
    tool = await mcp.get_tool("pricing_apply_subscription_prices")
    return await tool.fn(app_id=app_id, subscription_id=sub_id, request=request)


def _run(monkeypatch, path: str, request: dict, *,
         current: dict[str, float] = CURRENT, failing_calls: frozenset = frozenset()):
    """Apply ``request`` over ``current`` cached prices.

    Returns the ``preserveCurrentPrice`` sent per call, in item order, and
    the saved version's items by territory.
    """
    from app.mcp.tools import pricing as mcp_pricing
    from app.services.asc.errors import ASCAPIError
    from app.services.asc.pricing import ASCPricingService
    from test_pricing_fixes import _StubClient

    _patch_asc(monkeypatch, live_schedule=[])
    sent: list[bool] = []

    async def _create(self, subscription_id, price_point_id,
                      preserve_current_price=False):
        sent.append(preserve_current_price)
        if len(sent) - 1 in failing_calls:
            raise ASCAPIError(409, {"errors": [{"detail": "rejected"}]})
        return {}

    async def _client(app, session):
        return _StubClient()

    monkeypatch.setattr(ASCPricingService, "create_subscription_price", _create)
    monkeypatch.setattr(mcp_pricing, "_get_asc_client_for_app", _client)

    async def go():
        app_id, _, user_id = await _seed_iap_fixture()
        sub_id = await _seed_subscription(app_id)
        for code, price in current.items():
            await _cache_sub_price(sub_id, code, price, f"pp-{code}")
        if path == "rest":
            await _apply_rest(app_id, sub_id, user_id, request)
        else:
            await _apply_mcp(app_id, sub_id, user_id, request, monkeypatch)
        return await _versions(app_id, "subscription", sub_id)

    versions = run_async(go())
    return sent, _by_code(versions[0])


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_only_an_increase_keeps_existing_subscribers_on_their_price(
    monkeypatch, path,
):
    sent, saved = _run(monkeypatch, path, {
        "items": ITEMS, "preserve_current_price_on_increase": True,
    })
    assert sent == [True, False]
    assert saved["US"]["preserve_current_price"] is True
    assert saved["DE"]["preserve_current_price"] is False


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_without_the_flag_every_change_reaches_existing_subscribers(
    monkeypatch, path,
):
    sent, saved = _run(monkeypatch, path, {"items": ITEMS})
    assert sent == [False, False]
    assert saved["US"]["preserve_current_price"] is False


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_the_version_records_preserve_only_where_apple_accepted_it(
    monkeypatch, path,
):
    # US rises and lands; DE rises past the safety band and is skipped;
    # FR rises but Apple rejects it. Only US kept anyone on the old price.
    current = {"US": 3.99, "DE": 2.99, "FR": 3.99}
    sent, saved = _run(monkeypatch, path, {
        "items": [
            {"territory_code": code, "price_point_id": "pp-x"}
            for code in current
        ],
        "preserve_current_price_on_increase": True,
    }, current=current, failing_calls=frozenset({1}))
    assert sent == [True, True], "US and FR were sent, DE never was"
    assert saved["US"]["origin"] == "applied"
    assert saved["US"]["preserve_current_price"] is True
    assert saved["DE"]["origin"] == "skipped"
    assert saved["DE"]["preserve_current_price"] is False
    assert saved["FR"]["origin"] == "failed"
    assert saved["FR"]["preserve_current_price"] is False


def test_an_iap_apply_never_records_a_preserved_price(monkeypatch):
    from sqlalchemy import update

    from app.db.session import async_session_factory
    from app.models.iap import IAPPrice
    from test_pricing_fixes import _cache_prices
    from test_price_versions import _apply_iap

    _patch_asc(monkeypatch, live_schedule=[])

    async def go():
        app_id, iap_id, user_id = await _seed_iap_fixture()
        await _cache_prices(iap_id, ["US"])
        async with async_session_factory() as session:
            await session.execute(
                update(IAPPrice).where(IAPPrice.iap_id == iap_id)
                .values(customer_price=3.99)
            )
            await session.commit()
        await _apply_iap(app_id, iap_id, user_id, {
            "items": [{"territory_code": "US", "price_point_id": "pp-x"}],
            "preserve_current_price_on_increase": True,
        })
        return await _versions(app_id, "iap", iap_id)

    saved = _by_code(run_async(go())[0])
    assert saved["US"]["origin"] == "applied"
    assert saved["US"]["preserve_current_price"] is False
