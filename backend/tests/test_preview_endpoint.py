"""DB-backed preview test using the shared sync pytest async harness."""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401

from _async_harness import run_async
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.subscription import Subscription, SubscriptionGroup, SubscriptionPrice
from app.models.territory import Territory
from app.models.user import User
from app.services.pricing.currency_rounding import apply_currency_rounding


class _StaticRateClient:
    def __init__(self, rates: dict[str, float]) -> None:
        self._rates = rates
        self.calls: list[str] = []

    async def get_rates(self, base: str = "USD") -> dict[str, float]:
        self.calls.append(base)
        return self._rates


async def _reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed_preview_world() -> int:
    suffix = uuid.uuid4().hex[:8]

    async with async_session_factory() as session:
        us = Territory(code="US", name="United States", currency_code="USD", vat_rate=0.0)
        jp = Territory(code="JP", name="Japan", currency_code="JPY", vat_rate=0.10)
        br = Territory(code="BR", name="Brazil", currency_code="BRL", vat_rate=0.0)
        session.add_all([us, jp, br])
        await session.flush()

        user = User(
            email=f"preview-{suffix}@example.com",
            password_hash="not-used-by-this-test",
            name="Preview Test",
        )
        session.add(user)
        await session.flush()

        credential = ASCCredential(
            user_id=user.id,
            name="Preview ASC",
            issuer_id=f"issuer-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted="fixture-private-key",
        )
        session.add(credential)
        await session.flush()

        app = App(
            credential_id=credential.id,
            asc_app_id=f"adam-{suffix}",
            bundle_id=f"com.example.preview.{suffix}",
            name="Preview App",
            platform="ios",
        )
        session.add(app)
        await session.flush()

        group = SubscriptionGroup(
            app_id=app.id,
            asc_group_id=f"group-{suffix}",
            name="Premium",
        )
        session.add(group)
        await session.flush()

        subscription = Subscription(
            group_id=group.id,
            asc_subscription_id=f"sub-{suffix}",
            name="Monthly",
            product_id=f"com.example.preview.{suffix}.monthly",
        )
        session.add(subscription)
        await session.flush()

        session.add_all(
            [
                SubscriptionPrice(
                    subscription_id=subscription.id,
                    territory_id=us.id,
                    price_point_id="pp-us",
                    customer_price=2.99,
                    proceeds=2.09,
                ),
                SubscriptionPrice(
                    subscription_id=subscription.id,
                    territory_id=br.id,
                    price_point_id="pp-br",
                    customer_price=12.90,
                    proceeds=9.03,
                ),
            ]
        )
        await session.commit()
        return subscription.id


def test_preview_logic():
    """Exercise the DB-backed exchange-rate preview flow without pytest async markers."""

    async def go() -> tuple[list[dict[str, float | str | None]], list[str]]:
        await _reset_schema()
        subscription_id = await _seed_preview_world()

        async with async_session_factory() as session:
            result = await session.execute(select(Territory))
            territories = result.scalars().all()
            territory_map = {territory.code: territory for territory in territories}

            sub_result = await session.execute(
                select(Subscription).where(Subscription.id == subscription_id)
            )
            subscription = sub_result.scalar_one()

            prices_result = await session.execute(
                select(SubscriptionPrice).where(
                    SubscriptionPrice.subscription_id == subscription.id
                )
            )
            current_prices = prices_result.scalars().all()
            current_price_by_territory = {
                price.territory_id: price for price in current_prices
            }

        base_price = Decimal("2.99")
        base_territory = territory_map["US"]
        rate_client = _StaticRateClient({"JPY": 158.9200, "BRL": 5.0300})
        rates = await rate_client.get_rates(base=base_territory.currency_code)

        preview_items: list[dict[str, float | str | None]] = []
        for territory in territory_map.values():
            currency = territory.currency_code
            rate = 1.0 if currency == base_territory.currency_code else rates.get(currency)
            if rate is None:
                continue

            suggested_decimal = apply_currency_rounding(
                base_price * Decimal(str(rate)),
                currency,
            )
            current = current_price_by_territory.get(territory.id)

            preview_items.append(
                {
                    "territory_code": territory.code,
                    "currency_code": currency,
                    "current_price": current.customer_price if current else None,
                    "suggested_price": float(suggested_decimal),
                }
            )

        return preview_items, rate_client.calls

    preview_items, calls = run_async(go())

    assert calls == ["USD"]
    assert len(preview_items) == 3

    us_item = next(item for item in preview_items if item["territory_code"] == "US")
    assert us_item["current_price"] == 2.99
    assert abs(us_item["suggested_price"] - 2.99) < 0.001

    br_item = next(item for item in preview_items if item["territory_code"] == "BR")
    assert br_item["current_price"] == 12.90
    assert abs(br_item["suggested_price"] - 14.90) < 0.001

    jp_item = next(item for item in preview_items if item["territory_code"] == "JP")
    assert jp_item["current_price"] is None
    assert jp_item["suggested_price"] == 480.0
