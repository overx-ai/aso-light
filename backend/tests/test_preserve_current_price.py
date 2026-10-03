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


def _run(monkeypatch, path: str, request: dict):
    from app.mcp.tools import pricing as mcp_pricing
    from app.services.asc.pricing import ASCPricingService
    from test_pricing_fixes import _StubClient

    _patch_asc(monkeypatch, live_schedule=[])
    preserved: dict[str, bool] = {}

    async def _create(self, subscription_id, price_point_id,
                      preserve_current_price=False):
        preserved[f"call{len(preserved)}"] = preserve_current_price
        return {}

    async def _client(app, session):
        return _StubClient()

    monkeypatch.setattr(ASCPricingService, "create_subscription_price", _create)
    monkeypatch.setattr(mcp_pricing, "_get_asc_client_for_app", _client)

    async def go():
        app_id, _, user_id = await _seed_iap_fixture()
        sub_id = await _seed_subscription(app_id)
        for code, price in CURRENT.items():
            await _cache_sub_price(sub_id, code, price, f"pp-{code}")
        if path == "rest":
            await _apply_rest(app_id, sub_id, user_id, request)
        else:
            await _apply_mcp(app_id, sub_id, user_id, request, monkeypatch)
        return await _versions(app_id, "subscription", sub_id)

    versions = run_async(go())
    by_territory = dict(zip(CURRENT, preserved.values()))
    return by_territory, _by_code(versions[0])


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_only_an_increase_keeps_existing_subscribers_on_their_price(
    monkeypatch, path,
):
    sent, saved = _run(monkeypatch, path, {
        "items": ITEMS, "preserve_current_price_on_increase": True,
    })
    assert sent == {"US": True, "DE": False}
    assert saved["US"]["preserve_current_price"] is True
    assert saved["DE"]["preserve_current_price"] is False


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_without_the_flag_every_change_reaches_existing_subscribers(
    monkeypatch, path,
):
    sent, saved = _run(monkeypatch, path, {"items": ITEMS})
    assert sent == {"US": False, "DE": False}
    assert saved["US"]["preserve_current_price"] is False
