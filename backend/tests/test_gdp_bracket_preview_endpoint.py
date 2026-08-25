"""DB-backed integration test for the ``gdp_brackets`` preview branch.

``tests/test_gdp_brackets.py`` covers ``assign_tier()`` and the
``GDPBracketConfig`` validators as pure-function unit tests (including AC5,
the 422 on a ``gdp_brackets`` request with no ``gdp_config``). This module
closes the remaining gap: it drives the real ``preview_subscription_prices``
router function (same pattern as
``test_pricing_fixes.py::test_apply_iap_prices_refuses_when_cache_empty``)
end-to-end against a real SQLite session, seeding ``EconomicIndex`` rows of
type ``gdp_per_capita_ppp`` and asserting:

  * territories are bucketed into the correct tier and given that tier's
    absolute USD price, converted to local currency (AC3 from spec 005),
  * the shared ``±50%`` safety-band check (``exceeds_safety_band``) still
    fires for the ``gdp_brackets`` branch exactly as it does for the other
    strategies -- proving the "reuse existing safety bands" requirement
    actually holds for this branch and not just in theory (AC8).
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple, TypeVar
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401,E402

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from _async_harness import run_async  # noqa: E402
from app.api.v1.pricing import preview_subscription_prices  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.models.app import App  # noqa: E402
from app.models.credential import ASCCredential  # noqa: E402
from app.models.economic_index import EconomicIndex  # noqa: E402
from app.models.subscription import (  # noqa: E402
    Subscription,
    SubscriptionGroup,
    SubscriptionPrice,
)
from app.models.territory import Territory  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.pricing import GDPBracketConfig, PricePreviewRequest  # noqa: E402

T = TypeVar("T")

REFERENCE_DATE = date(2023, 1, 1)


class _SeedTerritory(NamedTuple):
    name: str
    currency: str
    #: ``None`` means "no ``EconomicIndex`` row at all" -> tier falls back to low.
    gdp_per_capita_ppp: float | None


#: One territory per branch of ``assign_tier()``'s priority order.
SEED_TERRITORIES: dict[str, _SeedTerritory] = {
    "US": _SeedTerritory("United States", "USD", 75000.0),  # top by GDP
    "DE": _SeedTerritory("Germany", "EUR", 20000.0),  # mid by GDP
    "IN": _SeedTerritory("India", "INR", 7000.0),  # low: under mid_min
    "ZZ": _SeedTerritory("No Data Land", "USD", None),  # no GDP row -> low
    "PL": _SeedTerritory("Poland", "PLN", 45000.0),  # top by GDP, but special
    "JP": _SeedTerritory("Japan", "JPY", 48000.0),  # top by GDP, but overridden
}

GDP_CONFIG = GDPBracketConfig(
    tier_prices_usd={
        "top": Decimal("9.99"),
        "mid": Decimal("4.99"),
        "low": Decimal("1.99"),
        "special": Decimal("2.99"),
    },
    tier_thresholds_usd={
        "top_min": Decimal("40000"),
        "mid_min": Decimal("15000"),
    },
    manual_overrides={"JP": "mid"},
    special_territories=["PL"],
)

FX_RATES = {"USD": 1.0, "EUR": 0.90, "INR": 83.0, "PLN": 4.0, "JPY": 150.0}

# US starts wildly out of band on purpose: $0.49 current vs. the $9.99 "top"
# tier price is a +1938% jump, far outside the shared ±50% safety band.
US_CURRENT_PRICE = 0.49


class _StaticRateClient:
    """Fixed USD-base FX rates so the test is deterministic and offline."""

    def __init__(self, rates: dict[str, float]) -> None:
        self._rates = rates

    async def get_rates(self, base: str = "USD") -> dict[str, float]:
        return self._rates


async def _add(session: AsyncSession, row: T) -> T:
    """Insert ``row`` and flush so its autoincrement id is populated."""
    session.add(row)
    await session.flush()
    return row


async def _seed_gdp_world() -> tuple[int, int, int]:
    """Rebuild the schema, seed the GDP world, return ``(app, sub, user)`` ids."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        territories = {
            code: Territory(
                code=code, name=seed.name, currency_code=seed.currency, vat_rate=0.0
            )
            for code, seed in SEED_TERRITORIES.items()
        }
        session.add_all(list(territories.values()))
        await session.flush()

        session.add_all(
            [
                EconomicIndex(
                    territory_id=territories[code].id,
                    index_type="gdp_per_capita_ppp",
                    value=seed.gdp_per_capita_ppp,
                    reference_date=REFERENCE_DATE,
                )
                for code, seed in SEED_TERRITORIES.items()
                if seed.gdp_per_capita_ppp is not None
            ]
        )

        user = await _add(
            session,
            User(
                email=f"gdp-{suffix}@example.com",
                password_hash="not-used-by-this-test",
                name="GDP Test",
            ),
        )
        credential = await _add(
            session,
            ASCCredential(
                user_id=user.id,
                name="GDP ASC",
                issuer_id=f"issuer-{suffix}",
                key_id=f"key-{suffix}",
                private_key_encrypted="fixture-private-key",
            ),
        )
        app_row = await _add(
            session,
            App(
                credential_id=credential.id,
                asc_app_id=f"adam-{suffix}",
                bundle_id=f"com.example.gdp.{suffix}",
                name="GDP App",
                platform="ios",
            ),
        )
        group = await _add(
            session,
            SubscriptionGroup(
                app_id=app_row.id,
                asc_group_id=f"group-{suffix}",
                name="Premium",
            ),
        )
        subscription = await _add(
            session,
            Subscription(
                group_id=group.id,
                asc_subscription_id=f"sub-{suffix}",
                name="Monthly",
                product_id=f"com.example.gdp.{suffix}.monthly",
            ),
        )

        session.add(
            SubscriptionPrice(
                subscription_id=subscription.id,
                territory_id=territories["US"].id,
                price_point_id="pp-us-old",
                customer_price=US_CURRENT_PRICE,
                proceeds=0.34,
            )
        )
        await session.commit()
        return app_row.id, subscription.id, user.id


def test_gdp_bracket_preview_tiers_and_safety_band():
    """AC3 + AC8: correct tier price per territory, safety band reused."""

    async def go():
        app_id, subscription_id, user_id = await _seed_gdp_world()

        body = PricePreviewRequest(
            index_type="gdp_brackets",
            base_territory_code="US",
            apply_vat=False,
            charming_mode="none",
            gdp_config=GDP_CONFIG,
        )

        async with async_session_factory() as session:
            with patch(
                "app.services.pricing.preview.RateCacheClient",
                return_value=_StaticRateClient(FX_RATES),
            ):
                return await preview_subscription_prices(
                    app_id=app_id,
                    subscription_id=subscription_id,
                    body=body,
                    current_user={"user_id": str(user_id)},
                    session=session,
                )

    response = run_async(go())
    items_by_code = {item.territory_code: item for item in response.items}

    # US: top tier ($9.99), rate 1.0 -> 9.99 USD.
    assert items_by_code["US"].suggested_price == 9.99
    # DE: mid tier ($4.99), rate 0.90 EUR -> 4.491, quantized to EUR's
    # 2-decimal profile (charming_mode="none") -> 4.49.
    assert items_by_code["DE"].suggested_price == 4.49
    # IN: low tier ($1.99, below mid_min threshold), rate 83.0 -> 165.17,
    # quantized to INR's 0-decimal profile (charming_mode="none") -> 165.
    assert items_by_code["IN"].suggested_price == 165.0
    # ZZ: no GDP data at all -> falls back to low ($1.99), USD rate 1.0.
    assert items_by_code["ZZ"].suggested_price == 1.99
    # PL: special list wins over its high GDP value -> special ($2.99).
    assert items_by_code["PL"].suggested_price == pytest.approx(2.99 * 4.0, abs=0.01)
    # JP: manual override to mid beats its high GDP value -> mid ($4.99),
    # rate 150.0 -> 748.5, quantized to JPY's 0-decimal profile (banker's
    # rounding: 748.5 -> 748).
    assert items_by_code["JP"].suggested_price == 748.0

    # AC8: US's current price ($0.49) vs. the new top-tier price ($9.99) is
    # a >1900% jump -- the shared ±50% safety band must flag it, proving
    # the gdp_brackets branch reuses exceeds_safety_band exactly like every
    # other strategy rather than bypassing it.
    assert items_by_code["US"].would_be_skipped is True
    # DE's current price is unset (no SubscriptionPrice row) so there is
    # nothing to compare against -- must not be skipped.
    assert items_by_code["DE"].would_be_skipped is False
