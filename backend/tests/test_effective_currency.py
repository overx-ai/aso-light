"""Tests for the per-territory currency resolver."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.pricing.currency import effective_currency


@dataclass
class _FakeTerritory:
    code: str
    currency_code: str


def test_cached_non_empty_currency_wins():
    territory = _FakeTerritory(code="BY", currency_code="BYN")
    cached = [{"currency_code": "USD", "customer_price": 0.29}]
    assert effective_currency(territory, cached) == "USD"


def test_empty_cached_currency_falls_back():
    territory = _FakeTerritory(code="BY", currency_code="BYN")
    cached = [{"currency_code": "", "customer_price": 0.29}]
    assert effective_currency(territory, cached) == "BYN"


def test_missing_currency_key_falls_back():
    territory = _FakeTerritory(code="BY", currency_code="BYN")
    cached = [{"customer_price": 0.29}]
    assert effective_currency(territory, cached) == "BYN"


def test_no_cache_falls_back():
    territory = _FakeTerritory(code="JP", currency_code="JPY")
    assert effective_currency(territory, None) == "JPY"


def test_empty_list_falls_back():
    territory = _FakeTerritory(code="JP", currency_code="JPY")
    assert effective_currency(territory, []) == "JPY"


def test_cached_currency_overrides_local_for_real_world_case():
    # User's reported case: BY served by Apple in USD despite our DB saying BYN.
    territory = _FakeTerritory(code="BY", currency_code="BYN")
    cached = [
        {"currency_code": "USD", "customer_price": 0.29, "price_point_id": "x"},
        {"currency_code": "USD", "customer_price": 0.39, "price_point_id": "y"},
    ]
    assert effective_currency(territory, cached) == "USD"


def test_cache_first_entry_decides():
    # First entry's currency drives the result; we trust Apple to return one currency.
    territory = _FakeTerritory(code="JP", currency_code="JPY")
    cached = [{"currency_code": "JPY", "customer_price": 50}]
    assert effective_currency(territory, cached) == "JPY"
