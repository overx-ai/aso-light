"""Every price apply is saved as a numbered version of its product.

A version holds the config that produced the prices (e.g. the GDP-bracket
tiers and special list) and the full per-territory list sent to Apple, so
any past state can be read back and re-applied. Before this, an apply
wrote nothing: the Lifetime IAP's tier config lived only in the web UI's
state and was lost the moment the tab closed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import app.models  # noqa: F401,E402

from _async_harness import run_async  # noqa: E402
from test_pricing_fixes import (  # noqa: E402
    _cache_prices,
    _patch_asc,
    _seed_iap_fixture,
)

GDP_CONFIG = {
    "index_type": "gdp_brackets",
    "base_price": 0.0,
    "base_territory_code": "US",
    "apply_vat": False,
    "charming_mode": "none",
    "gdp_config": {
        "tier_prices_usd": {
            "top": "59.99", "mid": "49.99", "low": "29.99", "special": "29.99",
        },
        "tier_thresholds_usd": {"top_min": "40000", "mid_min": "15000"},
        "manual_overrides": {"IE": "top"},
        "special_territories": ["GE", "UA"],
    },
}


async def _apply_iap(app_id, iap_id, user_id, request: dict):
    from app.api.v1.pricing import apply_iap_prices
    from app.db.session import async_session_factory
    from app.schemas.pricing import PriceApplyRequest

    async with async_session_factory() as session:
        return await apply_iap_prices(
            app_id=app_id,
            iap_id=iap_id,
            body=PriceApplyRequest.model_validate(request),
            current_user={"user_id": str(user_id)},
            session=session,
        )


async def _versions(app_id, kind, product_ref_id):
    from app.db.session import async_session_factory
    from app.services.pricing.versions import list_versions

    async with async_session_factory() as session:
        return await list_versions(
            session, app_id=app_id, product_kind=kind,
            product_ref_id=product_ref_id,
        )


def _by_code(version) -> dict[str, dict]:
    return {item["territory_code"]: item for item in version.items}


def test_iap_apply_saves_the_whole_schedule_and_its_config(monkeypatch):
    _patch_asc(monkeypatch, live_schedule=[])

    async def go():
        app_id, iap_id, user_id = await _seed_iap_fixture()
        await _cache_prices(iap_id, ["DE", "FR"])
        await _apply_iap(app_id, iap_id, user_id, {
            "items": [{"territory_code": "US", "price_point_id": "pp-x"}],
            "source_config": GDP_CONFIG,
            "note": "special tier fix",
        })
        return await _versions(app_id, "iap", iap_id)

    versions = run_async(go())
    assert [v.version for v in versions] == [1]
    v = versions[0]
    assert v.source == "api" and v.note == "special tier fix"
    assert v.base_territory_code == "US"
    assert v.result["applied"] == 1

    items = _by_code(v)
    assert set(items) == {"US", "DE", "FR"}, "Apple got the whole schedule"
    assert items["US"]["origin"] == "requested"
    assert items["US"]["price_point_id"] == "pp-x"
    assert items["US"]["customer_price"] == 4.99
    assert items["US"]["currency"] == "USD"
    assert items["DE"]["origin"] == "preserved"
    assert items["DE"]["price_point_id"] == "pp-de"
    assert items["DE"]["previous_price_point_id"] == "pp-de"
    assert items["DE"]["currency"] == "EUR"


def test_source_config_round_trips_with_tiers_and_special_list(monkeypatch):
    from app.schemas.pricing import PricePreviewRequest

    _patch_asc(monkeypatch, live_schedule=[])

    async def go():
        app_id, iap_id, user_id = await _seed_iap_fixture()
        await _apply_iap(app_id, iap_id, user_id, {
            "items": [{"territory_code": "US", "price_point_id": "pp-x"}],
            "source_config": GDP_CONFIG,
        })
        return await _versions(app_id, "iap", iap_id)

    stored = run_async(go())[0].config
    assert (PricePreviewRequest.model_validate(stored)
            == PricePreviewRequest.model_validate(GDP_CONFIG))
    assert stored["gdp_config"]["special_territories"] == ["GE", "UA"]
    assert stored["gdp_config"]["tier_prices_usd"]["special"] == "29.99"


def test_a_skipped_apply_is_still_a_version(monkeypatch):
    """The skip is the evidence; dropping it hides why prices didn't move."""
    from app.services.asc.price_point_cache import PricePointCache

    _patch_asc(monkeypatch, live_schedule=[])

    async def _tiers(self, alpha2, product_asc_id):
        return [{"price_point_id": "pp-big", "customer_price": 19.99,
                 "proceeds": 14.0, "currency_code": "EUR"}]

    monkeypatch.setattr(PricePointCache, "get_with_price_point_ids", _tiers)

    async def go():
        app_id, iap_id, user_id = await _seed_iap_fixture()
        await _cache_prices(iap_id, ["US", "DE"])
        result = await _apply_iap(app_id, iap_id, user_id, {
            "items": [{"territory_code": "DE", "price_point_id": "pp-big"}],
        })
        return result, await _versions(app_id, "iap", iap_id)

    result, versions = run_async(go())
    assert result.skipped == 1
    v = versions[0]
    assert v.config is None
    assert v.result["skipped"] == 1
    de = _by_code(v)["DE"]
    assert de["origin"] == "skipped"
    assert de["customer_price"] == 19.99
    assert de["previous_customer_price"] == 4.99


def test_versions_count_per_product(monkeypatch):
    _patch_asc(monkeypatch, live_schedule=[])

    async def go():
        from app.db.session import async_session_factory
        from app.models.iap import InAppPurchase

        app_id, iap_id, user_id = await _seed_iap_fixture()
        async with async_session_factory() as session:
            other = InAppPurchase(
                app_id=app_id, asc_iap_id="iap-other", name="Other",
                product_id="com.example.other", iap_type="NON_CONSUMABLE",
            )
            session.add(other)
            await session.commit()
            other_id = other.id
        request = {"items": [{"territory_code": "US", "price_point_id": "pp-x"}]}
        await _apply_iap(app_id, iap_id, user_id, request)
        await _apply_iap(app_id, iap_id, user_id, request)
        await _apply_iap(app_id, other_id, user_id, request)
        return (await _versions(app_id, "iap", iap_id),
                await _versions(app_id, "iap", other_id))

    first, other = run_async(go())
    assert [v.version for v in first] == [2, 1], "newest first"
    assert [v.version for v in other] == [1]


async def _seed_subscription(app_id: int) -> int:
    from app.db.session import async_session_factory
    from app.models.subscription import Subscription, SubscriptionGroup

    async with async_session_factory() as session:
        group = SubscriptionGroup(app_id=app_id, asc_group_id="grp-1",
                                  name="Premium")
        session.add(group)
        await session.flush()
        sub = Subscription(group_id=group.id, asc_subscription_id="sub-1",
                           name="Yearly", product_id="com.example.yearly")
        session.add(sub)
        await session.commit()
        return sub.id


async def _cache_sub_price(sub_id: int, code: str, price: float, ppid: str):
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.subscription import SubscriptionPrice
    from app.models.territory import Territory

    async with async_session_factory() as session:
        territory = (await session.execute(
            select(Territory).where(Territory.code == code)
        )).scalar_one()
        session.add(SubscriptionPrice(
            subscription_id=sub_id, territory_id=territory.id,
            price_point_id=ppid, customer_price=price, proceeds=price * 0.7,
            synced_at=datetime.now(timezone.utc),
        ))
        await session.commit()


def test_mcp_subscription_apply_saves_previous_prices(monkeypatch):
    from app.mcp import context as mcp_context
    from app.mcp.server import mcp
    from app.mcp.tools import pricing as mcp_pricing
    from app.services.asc.pricing import ASCPricingService

    _patch_asc(monkeypatch, live_schedule=[])
    created: list[str] = []

    async def _create(self, subscription_id, price_point_id, **_):
        created.append(price_point_id)
        return {}

    async def _client(app, session):
        from test_pricing_fixes import _StubClient
        return _StubClient()

    monkeypatch.setattr(ASCPricingService, "create_subscription_price", _create)
    monkeypatch.setattr(mcp_pricing, "_get_asc_client_for_app", _client)

    async def go():
        app_id, _, user_id = await _seed_iap_fixture()
        monkeypatch.setattr(mcp_context, "get_user_id", lambda: user_id)
        sub_id = await _seed_subscription(app_id)
        await _cache_sub_price(sub_id, "US", 3.99, "pp-old")
        tool = await mcp.get_tool("pricing_apply_subscription_prices")
        await tool.fn(app_id=app_id, subscription_id=sub_id, request={
            "items": [{"territory_code": "US", "price_point_id": "pp-x"}],
            "source_config": GDP_CONFIG,
        })
        listed = await (await mcp.get_tool("pricing_list_price_versions")).fn(
            app_id=app_id, product_kind="subscription", product_ref_id=sub_id,
        )
        one = await (await mcp.get_tool("pricing_get_price_version")).fn(
            app_id=app_id, version_id=listed[0].id,
        )
        return listed, one

    listed, one = run_async(go())
    assert created == ["pp-x"]
    assert [v.version for v in listed] == [1]
    assert one.source == "mcp"
    assert one.config["gdp_config"]["special_territories"] == ["GE", "UA"]
    us = _by_code(one)["US"]
    assert us["previous_customer_price"] == 3.99
    assert us["previous_price_point_id"] == "pp-old"
    assert us["customer_price"] == 4.99


def test_snapshot_records_the_cached_prices_as_a_baseline(monkeypatch):
    from app.mcp import context as mcp_context
    from app.mcp.server import mcp

    async def go():
        app_id, iap_id, user_id = await _seed_iap_fixture()
        monkeypatch.setattr(mcp_context, "get_user_id", lambda: user_id)
        await _cache_prices(iap_id, ["US", "DE"])
        tool = await mcp.get_tool("pricing_snapshot_price_version")
        return await tool.fn(app_id=app_id, product_kind="iap",
                             product_ref_id=iap_id, note="before fix")

    v = run_async(go())
    assert v.version == 1 and v.source == "baseline" and v.note == "before fix"
    assert {c: i["origin"] for c, i in _by_code(v).items()} == {
        "US": "current", "DE": "current",
    }


def test_version_reads_are_read_only_and_the_snapshot_is_not_destructive():
    from app.mcp.consent import DESTRUCTIVE, READ_ONLY

    assert {"pricing_list_price_versions",
            "pricing_get_price_version"} <= READ_ONLY
    assert "pricing_snapshot_price_version" not in READ_ONLY
    assert "pricing_snapshot_price_version" not in DESTRUCTIVE
