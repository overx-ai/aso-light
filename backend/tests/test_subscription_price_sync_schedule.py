"""A subscription's current price is the schedule row in effect today (docs/bugs/014).

Apple lists every row of a price schedule: the undated initial price,
past changes and changes that have not started yet. The sync used to keep
whichever came last, from the first 200 rows only.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from _async_harness import run_async  # noqa: E402

TODAY = date(2026, 10, 3)


def _row(rid, territory, pp, start):
    return {
        "id": rid, "type": "subscriptionPrices",
        "attributes": {"startDate": start, "preserved": False},
        "relationships": {
            "subscriptionPricePoint": {"data": {"type": "subscriptionPricePoints", "id": pp}},
            "territory": {"data": {"type": "territories", "id": territory}},
        },
    }


def _pp(pp, price):
    return {"type": "subscriptionPricePoints", "id": pp,
            "attributes": {"customerPrice": str(price), "proceeds": "1"}}


PAGES = {
    "first": {
        "data": [
            # The scheduled row is listed last: it must not win.
            _row("r1", "BTN", "pp-btn-old", None),
            _row("r2", "BTN", "pp-btn-new", "2026-10-04"),
            _row("r3", "ZAF", "pp-zaf", None),
        ],
        "included": [_pp("pp-btn-old", 1.99), _pp("pp-btn-new", 2.99),
                     _pp("pp-zaf", 32.99)],
        "links": {"next": "https://api.example/next"},
    },
    "https://api.example/next": {
        "data": [
            _row("r4", "GEO", "pp-geo-change", "2026-10-01"),
            _row("r5", "GEO", "pp-geo-initial", None),
        ],
        "included": [_pp("pp-geo-change", 0.99), _pp("pp-geo-initial", 2.99)],
        "links": {},
    },
}


class _Client:
    async def _get(self, path, params=None):
        return PAGES["first" if path.startswith("/subscriptions/") else path]


def test_the_sync_keeps_the_row_in_effect_on_every_page():
    from app.services.asc.pricing import ASCPricingService

    rows = run_async(
        ASCPricingService(_Client()).get_subscription_prices("sub-1", today=TODAY)
    )
    current = {r["territory_code"]: r["customer_price"] for r in rows}
    assert current == {"BTN": 1.99, "ZAF": 32.99, "GEO": 0.99}
    assert len(rows) == 3


class _OnePage:
    def __init__(self, page):
        self.page = page

    async def _get(self, path, params=None):
        return self.page


def test_a_change_starting_today_is_in_effect_and_a_future_only_territory_is_unpriced():
    from app.services.asc.pricing import ASCPricingService

    page = {
        "data": [
            _row("r1", "BTN", "pp-btn-old", None),
            _row("r2", "BTN", "pp-btn-new", TODAY.isoformat()),
            _row("r3", "NPL", "pp-npl", "2026-10-04"),
        ],
        "included": [_pp("pp-btn-old", 1.99), _pp("pp-btn-new", 2.99),
                     _pp("pp-npl", 4.99)],
        "links": {},
    }
    rows = run_async(
        ASCPricingService(_OnePage(page)).get_subscription_prices("sub-1", today=TODAY)
    )
    assert {r["territory_code"]: r["customer_price"] for r in rows} == {"BTN": 2.99}


class _FailingNextPage:
    async def _get(self, path, params=None):
        from app.services.asc.errors import ASCAPIError

        if path.startswith("/subscriptions/"):
            return PAGES["first"]
        raise ASCAPIError(500, {"errors": [{"detail": "server error"}]})


def test_a_later_page_that_fails_raises_instead_of_truncating():
    from app.services.asc.errors import ASCAPIError
    from app.services.asc.pricing import ASCPricingService

    with pytest.raises(ASCAPIError):
        run_async(
            ASCPricingService(_FailingNextPage()).get_subscription_prices(
                "sub-1", today=TODAY,
            )
        )
