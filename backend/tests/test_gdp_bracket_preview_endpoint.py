"""DB-backed integration test for the ``gdp_brackets`` preview branch.

``tests/test_gdp_brackets.py`` covers ``assign_tier()`` and the
``GDPBracketConfig`` validators as pure-function unit tests. This module
closes the remaining gap: it drives the real
``preview_subscription_prices`` router function (same pattern as
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

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401,E402

from _async_harness import run_async  # noqa: E402


class _StaticRateClient:
    """Fixed USD-base FX rates so the test is deterministic and offline."""

    def __init__(self, rates: dict[str, float]) -> None:
        self._rates = rates

    async def get_rates(self, base: str = "USD") -> dict[str, float]:
        return self._rates


def _seed_gdp_world():
    """Build the async world-setup coroutine (imports deferred to inside ``go``)."""

    async def go():
        from app.db.base import Base
        from app.db.session import async_session_factory, engine
        from app.models.app import App
        from app.models.credential import ASCCredential
        from app.models.economic_index import EconomicIndex
        from app.models.subscription import (
            Subscription,
            SubscriptionGroup,
            SubscriptionPrice,
        )
        from app.models.territory import Territory
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        suffix = uuid.uuid4().hex[:8]
        async with async_session_factory() as session:
            # US: top tier by GDP. DE: mid tier by GDP. IN: low tier (below
            # mid_min). ZZ: no GDP data at all -> falls back to low. PL:
            # would be top by GDP but is on the special list. JP: would be
            # top by GDP but has a manual override pinning it to mid.
            us = Territory(code="US", name="United States", currency_code="USD", vat_rate=0.0)
            de = Territory(code="DE", name="Germany", currency_code="EUR", vat_rate=0.0)
            india = Territory(code="IN", name="India", currency_code="INR", vat_rate=0.0)
            zz = Territory(code="ZZ", name="No Data Land", currency_code="USD", vat_rate=0.0)
            pl = Territory(code="PL", name="Poland", currency_code="PLN", vat_rate=0.0)
            jp = Territory(code="JP", name="Japan", currency_code="JPY", vat_rate=0.0)
            session.add_all([us, de, india, zz, pl, jp])
            await session.flush()

            ref_date = date(2023, 1, 1)
            session.add_all(
                [
                    EconomicIndex(
                        territory_id=us.id, index_type="gdp_per_capita_ppp",
                        value=75000.0, reference_date=ref_date,
                    ),
                    EconomicIndex(
                        territory_id=de.id, index_type="gdp_per_capita_ppp",
                        value=20000.0, reference_date=ref_date,
                    ),
                    EconomicIndex(
                        territory_id=india.id, index_type="gdp_per_capita_ppp",
                        value=7000.0, reference_date=ref_date,
                    ),
                    EconomicIndex(
                        territory_id=pl.id, index_type="gdp_per_capita_ppp",
                        value=45000.0, reference_date=ref_date,  # would be "top" if not special
                    ),
                    EconomicIndex(
                        territory_id=jp.id, index_type="gdp_per_capita_ppp",
                        value=48000.0, reference_date=ref_date,  # would be "top" w/o override
                    ),
                    # ZZ intentionally has no EconomicIndex row.
                ]
            )
            await session.flush()

            user = User(
                email=f"gdp-{suffix}@example.com",
                password_hash="not-used-by-this-test",
                name="GDP Test",
            )
            session.add(user)
            await session.flush()

            credential = ASCCredential(
                user_id=user.id,
                name="GDP ASC",
                issuer_id=f"issuer-{suffix}",
                key_id=f"key-{suffix}",
                private_key_encrypted="fixture-private-key",
            )
            session.add(credential)
            await session.flush()

            app_row = App(
                credential_id=credential.id,
                asc_app_id=f"adam-{suffix}",
                bundle_id=f"com.example.gdp.{suffix}",
                name="GDP App",
                platform="ios",
            )
            session.add(app_row)
            await session.flush()

            group = SubscriptionGroup(
                app_id=app_row.id,
                asc_group_id=f"group-{suffix}",
                name="Premium",
            )
            session.add(group)
            await session.flush()

            subscription = Subscription(
                group_id=group.id,
                asc_subscription_id=f"sub-{suffix}",
                name="Monthly",
                product_id=f"com.example.gdp.{suffix}.monthly",
            )
            session.add(subscription)
            await session.flush()

            # US currently has a wildly out-of-band price so the safety
            # check must fire: current $0.49 vs. a $9.99 "top" tier price
            # is a +1938% jump, far outside +20%/-25%.
            session.add(
                SubscriptionPrice(
                    subscription_id=subscription.id,
                    territory_id=us.id,
                    price_point_id="pp-us-old",
                    customer_price=0.49,
                    proceeds=0.34,
                )
            )
            await session.commit()
            return app_row.id, subscription.id, user.id

    return go()


def test_gdp_bracket_preview_tiers_and_safety_band():
    """AC3 + AC8: correct tier price per territory, safety band reused."""

    async def go():
        from unittest.mock import patch

        from app.api.v1.pricing import preview_subscription_prices
        from app.db.session import async_session_factory
        from app.schemas.pricing import GDPBracketConfig, PricePreviewRequest

        app_id, subscription_id, user_id = await _seed_gdp_world()

        gdp_config = GDPBracketConfig(
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
        body = PricePreviewRequest(
            index_type="gdp_brackets",
            base_territory_code="US",
            apply_vat=False,
            charming_mode="none",
            gdp_config=gdp_config,
        )

        rate_client = _StaticRateClient(
            {"USD": 1.0, "EUR": 0.90, "INR": 83.0, "PLN": 4.0, "JPY": 150.0}
        )

        async with async_session_factory() as session:
            with patch(
                "app.services.pricing.preview.RateCacheClient",
                return_value=rate_client,
            ):
                response = await preview_subscription_prices(
                    app_id=app_id,
                    subscription_id=subscription_id,
                    body=body,
                    current_user={"user_id": str(user_id)},
                    session=session,
                )
        return response

    response = run_async(go())
    items_by_code = {item.territory_code: item for item in response.items}

    # US: top tier ($9.99), rate 1.0 -> 9.99 USD.
    assert items_by_code["US"].suggested_price == 9.99
    # DE: mid tier ($4.99), rate 0.90 EUR -> 4.491.
    assert abs(items_by_code["DE"].suggested_price - 4.491) < 0.001
    # IN: low tier ($1.99, below mid_min threshold), rate 83.0 -> 165.17,
    # quantized to INR's 0-decimal profile (charming_mode="none") -> 165.
    assert items_by_code["IN"].suggested_price == 165.0
    # ZZ: no GDP data at all -> falls back to low ($1.99), USD rate 1.0.
    assert items_by_code["ZZ"].suggested_price == 1.99
    # PL: special list wins over its high GDP value -> special ($2.99).
    assert abs(items_by_code["PL"].suggested_price - (2.99 * 4.0)) < 0.01
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


def test_gdp_bracket_preview_requires_gdp_config():
    """AC5 exercised at the router boundary: a bare gdp_brackets preview
    request without gdp_config is rejected by Pydantic before it ever
    reaches the DB, so the router never even needs the safety branch below.
    """
    from pydantic import ValidationError

    from app.schemas.pricing import PricePreviewRequest

    try:
        PricePreviewRequest(index_type="gdp_brackets", base_territory_code="US")
    except ValidationError as exc:
        assert "gdp_config is required" in str(exc)
    else:
        raise AssertionError("expected ValidationError for missing gdp_config")
