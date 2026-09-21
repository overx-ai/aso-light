"""Tests for the /code-pass pricing fixes.

Covers:
  * Economic-index preview now returns Decimal-derived, currency-rounded
    prices (no float ``.99`` suffix on 0-decimal currencies; valid
    3-decimal prices for KWD).
  * ``finalize_price`` charm modes are currency-aware (JPY/KRW/KWD).
  * ``exceeds_safety_band`` Decimal boundary (exactly ±50% passes).
  * 3-decimal currency rounding stays within the ±10% band.
  * IAP apply refuses (409 / ToolError) when the price cache is empty but
    Apple holds prices, and proceeds when neither side has any.
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401,E402

from _async_harness import run_async  # noqa: E402
from app.services.pricing.currency_rounding import (  # noqa: E402
    CURRENCY_PROFILES,
    apply_currency_rounding,
)
from app.services.pricing.preview import (  # noqa: E402
    economic_index_suggested,
    finalize_price,
)
from app.services.pricing.safety import exceeds_safety_band  # noqa: E402


# ------------------------------------------------------------------
# C1 / C2: economic-index preview is Decimal + currency-aware
# ------------------------------------------------------------------


def test_economic_index_suggested_returns_decimal():
    """Economic-index branch returns a Decimal, not a float."""
    result = economic_index_suggested(
        base_price=2.99,
        territory_index=80.0,
        base_index_value=100.0,
        index_type="ppp",
        vat_rate=None,
        apply_vat_flag=False,
        charming_mode="none",
        currency_code="USD",
    )
    assert isinstance(result, Decimal)


def test_economic_index_jpy_no_float_99_suffix():
    """JPY (0-decimal) must never get a ``.99``/``.95`` 2-decimal suffix."""
    for mode in ("99", "95", "smart", "none"):
        result = economic_index_suggested(
            base_price=4.99,
            territory_index=300.0,  # ~JPY 1497 raw
            base_index_value=1.0,
            index_type="ppp",
            vat_rate=None,
            apply_vat_flag=False,
            charming_mode=mode,
            currency_code="JPY",
        )
        # Whole number — JPY has zero minor units.
        assert result == result.to_integral_value(), (mode, result)


def test_economic_index_krw_no_float_99_suffix():
    """KRW (0-decimal) stays whole across every charm mode."""
    for mode in ("99", "95", "smart", "none"):
        result = economic_index_suggested(
            base_price=9.99,
            territory_index=1500.0,
            base_index_value=1.0,
            index_type="ppp",
            vat_rate=None,
            apply_vat_flag=False,
            charming_mode=mode,
            currency_code="KRW",
        )
        assert result == result.to_integral_value(), (mode, result)


def test_economic_index_kwd_valid_three_decimal():
    """KWD (3-decimal) gets a price with at most 3 fractional digits."""
    for mode in ("99", "95", "smart", "none"):
        result = economic_index_suggested(
            base_price=4.99,
            territory_index=2.0,
            base_index_value=1.0,
            index_type="ppp",
            vat_rate=None,
            apply_vat_flag=False,
            charming_mode=mode,
            currency_code="KWD",
        )
        exponent = -result.as_tuple().exponent
        assert exponent <= 3, (mode, result)
        assert result > 0


def test_economic_index_honors_vat():
    """apply_vat is now respected in the economic-index branch."""
    no_vat = economic_index_suggested(
        base_price=10.0,
        territory_index=1.0,
        base_index_value=1.0,
        index_type="ppp",
        vat_rate=0.20,
        apply_vat_flag=False,
        charming_mode="none",
        currency_code="USD",
    )
    with_vat = economic_index_suggested(
        base_price=10.0,
        territory_index=1.0,
        base_index_value=1.0,
        index_type="ppp",
        vat_rate=0.20,
        apply_vat_flag=True,
        charming_mode="none",
        currency_code="USD",
    )
    assert with_vat > no_vat
    assert with_vat == Decimal("12.00")


def test_finalize_price_usd_charm():
    # finalize_price delegates 2-decimal currencies to apply_charming.
    assert finalize_price(Decimal("2.50"), "99", "USD") == Decimal("2.99")
    assert finalize_price(Decimal("2.50"), "95", "USD") == Decimal("2.95")
    assert finalize_price(Decimal("2.10"), "95", "USD") == Decimal("1.95")
    assert finalize_price(Decimal("2.50"), "none", "USD") == Decimal("2.50")


def test_finalize_price_jpy_modes_whole():
    for mode in ("99", "95", "smart", "none"):
        out = finalize_price(Decimal("1490.4"), mode, "JPY")
        assert out == out.to_integral_value(), (mode, out)


# ------------------------------------------------------------------
# I3: safety band in Decimal, exact ±50% allowed
# ------------------------------------------------------------------


def test_exceeds_safety_band_exact_boundary_passes():
    # Exactly +50% and -50% are allowed (strict comparison).
    assert exceeds_safety_band(Decimal("10"), Decimal("15")) is False
    assert exceeds_safety_band(Decimal("10"), Decimal("5")) is False


def test_exceeds_safety_band_just_over_skips():
    assert exceeds_safety_band(Decimal("10"), Decimal("15.01")) is True
    assert exceeds_safety_band(Decimal("10"), Decimal("4.99")) is True


def test_exceeds_safety_band_accepts_float():
    # Floats are coerced via str; the classic 0.1+0.2 case must not leak.
    assert exceeds_safety_band(10.0, 15.0) is False
    assert exceeds_safety_band(10.0, 15.0001) is True


# ------------------------------------------------------------------
# C3: 3-decimal currency rounding inside ±10% band
# ------------------------------------------------------------------


def test_three_decimal_currencies_use_three_decimals():
    for code in ("KWD", "BHD", "OMR", "JOD", "TND"):
        assert CURRENCY_PROFILES[code].decimals == 3


def test_three_decimal_rounding_within_flex_band():
    flex = Decimal("0.10")
    for code in ("KWD", "BHD", "OMR", "JOD", "TND"):
        for raw in (Decimal("0.50"), Decimal("1.23"), Decimal("3.70"),
                    Decimal("9.99")):
            out = apply_currency_rounding(raw, code)
            exponent = -out.as_tuple().exponent
            assert exponent <= 3, (code, raw, out)
            # Within ±10% of the raw value (or at the profile minimum).
            lower = raw * (Decimal("1") - flex)
            upper = raw * (Decimal("1") + flex)
            min_price = Decimal(CURRENCY_PROFILES[code].min_price)
            assert lower <= out <= upper or out == min_price, (code, raw, out)


def test_three_decimal_charm_suffix_is_x99():
    # 2.000 -> 1.999 (one decimal place further right than USD's .99).
    assert apply_currency_rounding(Decimal("2.00"), "KWD") == Decimal("1.999")


# ------------------------------------------------------------------
# I4: IAP apply and the empty price cache
#
# An empty ``IAPPrice`` cache has two causes the DB cannot tell apart:
# the IAP has prices at Apple we never synced (applying would wipe every
# territory not in this request), or it has none at all (nothing to
# preserve). Only Apple knows which, so the guard asks. These two tests
# pin both answers — the second is the one a freshly created IAP hits.
# ------------------------------------------------------------------


async def _seed_iap_fixture() -> tuple[int, int, int]:
    """Create user → credential → app → IAP with no cached prices.

    Returns ``(app_id, iap_id, user_id)``.
    """
    from app.db.base import Base
    from app.db.session import async_session_factory, engine
    from app.models.app import App
    from app.models.credential import ASCCredential
    from app.models.iap import InAppPurchase
    from app.models.territory import Territory
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        session.add_all([
            Territory(code="US", name="United States",
                      currency_code="USD", vat_rate=0.0),
            Territory(code="DE", name="Germany",
                      currency_code="EUR", vat_rate=0.0),
            Territory(code="FR", name="France",
                      currency_code="EUR", vat_rate=0.0),
        ])
        user = User(
            email=f"iap-{suffix}@example.com",
            password_hash="x",
            name="IAP Test",
        )
        session.add(user)
        await session.flush()

        credential = ASCCredential(
            user_id=user.id,
            name="ASC",
            issuer_id=f"iss-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted="fixture",
        )
        session.add(credential)
        await session.flush()

        app = App(
            credential_id=credential.id,
            asc_app_id=f"adam-{suffix}",
            bundle_id=f"com.example.iap.{suffix}",
            name="IAP App",
            platform="ios",
        )
        session.add(app)
        await session.flush()

        iap = InAppPurchase(
            app_id=app.id,
            asc_iap_id=f"iap-{suffix}",
            name="Coins",
            product_id=f"com.example.iap.{suffix}.coins",
            iap_type="CONSUMABLE",
        )
        session.add(iap)
        await session.commit()
        return app.id, iap.id, user.id


class _StubClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_asc(monkeypatch, *, live_schedule: list[dict]) -> list[dict]:
    """Stub the ASC client, schedule read, tier ladder and the write.

    Returns the list that captures ``set_iap_price`` calls.
    """
    from app.api.v1 import pricing as pricing_routes
    from app.services.asc.price_point_cache import PricePointCache
    from app.services.asc.pricing import ASCPricingService

    submitted: list[dict] = []

    async def _client(app, session):
        return _StubClient()

    async def _schedule(self, iap_id):
        return live_schedule

    async def _tiers(self, alpha2, product_asc_id):
        return [{
            "price_point_id": "pp-x",
            "customer_price": 4.99,
            "proceeds": 3.49,
            "currency_code": "USD",
        }]

    async def _set(self, iap_id, price_entries, base_territory_alpha3="USA"):
        submitted.append({
            "iap_id": iap_id,
            "entries": price_entries,
            "base": base_territory_alpha3,
        })
        return {}

    monkeypatch.setattr(pricing_routes, "_get_asc_client_for_app", _client)
    monkeypatch.setattr(
        ASCPricingService, "get_iap_price_schedule", _schedule,
    )
    monkeypatch.setattr(
        PricePointCache, "get_with_price_point_ids", _tiers,
    )
    monkeypatch.setattr(ASCPricingService, "set_iap_price", _set)
    return submitted


async def _cache_prices(iap_id: int, codes: list[str]) -> None:
    """Give the IAP a synced ``IAPPrice`` row per territory code."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.iap import IAPPrice
    from app.models.territory import Territory

    async with async_session_factory() as session:
        result = await session.execute(
            select(Territory).where(Territory.code.in_(codes))
        )
        for territory in result.scalars().all():
            session.add(IAPPrice(
                iap_id=iap_id,
                territory_id=territory.id,
                price_point_id=f"pp-{territory.code.lower()}",
                customer_price=4.99,
                proceeds=3.49,
                synced_at=datetime.now(timezone.utc),
            ))
        await session.commit()


async def _seed_then_apply_us_price(cached: list[str] | None = None):
    """Seed a fresh IAP and apply a single US price to it."""
    from app.api.v1.pricing import apply_iap_prices
    from app.db.session import async_session_factory
    from app.schemas.pricing import PriceApplyItem, PriceApplyRequest

    app_id, iap_id, user_id = await _seed_iap_fixture()
    if cached:
        await _cache_prices(iap_id, cached)
    body = PriceApplyRequest(
        items=[PriceApplyItem(territory_code="US", price_point_id="pp-x")]
    )
    async with async_session_factory() as session:
        return await apply_iap_prices(
            app_id=app_id,
            iap_id=iap_id,
            body=body,
            current_user={"user_id": str(user_id)},
            session=session,
        )


def test_apply_iap_prices_refuses_when_apple_has_unsynced_prices(monkeypatch):
    """The guard's real case: Apple holds prices our cache has never seen."""
    from fastapi import HTTPException

    submitted = _patch_asc(monkeypatch, live_schedule=[
        {"territory_code": "DE", "customer_price": 4.99,
         "proceeds": 3.49, "currency_code": "EUR", "price_point_id": "pp-de"},
    ])

    with pytest.raises(HTTPException) as err:
        run_async(_seed_then_apply_us_price())

    assert err.value.status_code == 409
    assert "Sync IAP prices" in str(err.value.detail)
    assert submitted == [], "refused, but still wrote to Apple"


def test_apply_iap_prices_proceeds_on_a_never_priced_iap(monkeypatch):
    """A freshly created IAP must be priceable with no prior sync.

    Empty cache + no schedule at Apple means there is nothing to preserve,
    so the guard has nothing to protect. Refusing here made every IAP
    created through the API unfinishable.
    """
    submitted = _patch_asc(monkeypatch, live_schedule=[])

    result = run_async(_seed_then_apply_us_price())
    assert result.applied == 1, (result.applied, result.errors)
    assert result.failed == 0 and result.skipped == 0

    assert len(submitted) == 1
    # Only what was asked for — the preserve loop had nothing to pad with.
    assert submitted[0]["entries"] == [
        {"territory_code": "US", "price_point_id": "pp-x"},
    ]
    assert submitted[0]["base"] == "USA"


def test_apply_iap_prices_preserves_untouched_territories(monkeypatch):
    """The whole reason the guard exists: Apple replaces the entire schedule.

    Applying US alone must re-submit the cached DE/FR prices verbatim, or
    those two live territories silently revert to auto-equalization. The
    stubbed schedule is deliberately non-empty: if the guard ever consulted
    Apple on a warm cache this would 409 instead of asserting below.
    """
    submitted = _patch_asc(monkeypatch, live_schedule=[
        {"territory_code": "DE", "customer_price": 4.99,
         "proceeds": 3.49, "currency_code": "EUR", "price_point_id": "pp-de"},
    ])

    result = run_async(_seed_then_apply_us_price(cached=["DE", "FR"]))

    # Preserved territories are padding, not changes.
    assert result.applied == 1, (result.applied, result.errors)

    entries = {e["territory_code"]: e["price_point_id"]
               for e in submitted[0]["entries"]}
    assert entries == {
        "US": "pp-x", "DE": "pp-de", "FR": "pp-fr",
    }
