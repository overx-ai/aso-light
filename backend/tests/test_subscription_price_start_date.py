"""A price change to an approved subscription is dated (docs/bugs/013).

Apple keeps one undated price per territory, the initial one. A change
without ``startDate`` is read as a second initial price and refused once
the subscription is approved, so every apply failed on a live product.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import app.models  # noqa: F401,E402

from _async_harness import run_async  # noqa: E402


@pytest.mark.parametrize("now, expected", [
    # 00:03 UTC on the 4th is still the 3rd in California.
    (datetime(2026, 10, 4, 0, 3, tzinfo=timezone.utc), date(2026, 10, 4)),
    (datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc), date(2026, 10, 4)),
    (datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc), date(2026, 10, 5)),
])
def test_a_change_starts_on_the_next_pacific_day(now, expected):
    from app.services.asc.pricing import next_price_change_date

    assert next_price_change_date(now) == expected


def test_a_dated_price_carries_its_start_date_and_an_initial_one_does_not():
    from app.services.asc.pricing import ASCPricingService

    sent: list[dict] = []

    class _Client:
        async def _post(self, path, json):
            sent.append(json["data"]["attributes"])
            return {}

    service = ASCPricingService(_Client())

    async def go():
        await service.create_subscription_price(
            "sub-1", "pp-1", start_date=date(2026, 10, 4),
        )
        await service.create_subscription_price("sub-1", "pp-1")

    run_async(go())
    assert sent[0]["startDate"] == "2026-10-04"
    assert "startDate" not in sent[1]


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_the_apply_dates_a_change_and_leaves_a_first_price_undated(
    monkeypatch, path,
):
    from app.services.asc.pricing import next_price_change_date
    from test_preserve_current_price import _run

    starts: list = []
    sent, saved = _run(
        monkeypatch, path,
        {"items": [
            {"territory_code": "US", "price_point_id": "pp-x"},
            {"territory_code": "DE", "price_point_id": "pp-x"},
        ]},
        current={"US": 3.99},
        starts=starts,
    )
    assert starts == [next_price_change_date(), None]
