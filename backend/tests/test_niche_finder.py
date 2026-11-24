from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.mcp.server import mcp
from app.services.keyword_intel import free, niche
from app.services.keyword_intel.niche import (
    NICHE_MAX_REAL_APPS,
    REAL_APP_MIN_RATINGS,
    STALE_DAYS,
    NicheFinder,
    classify_supply,
    opportunity,
)
from tests._async_harness import run_async

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=30)).isoformat()
STALE = (NOW - timedelta(days=STALE_DAYS + 30)).isoformat()


def _app(name: str, ratings: int = REAL_APP_MIN_RATINGS * 5, updated: str | None = FRESH) -> dict:
    return {"name": name, "rating_count": ratings, "updated_at": updated}


class _FakeHints:
    def __init__(self, by_prefix: dict[str, list[str]]) -> None:
        self.by_prefix = by_prefix
        self.calls: list[str] = []

    async def get_suggestions(self, term: str, country: str = "us") -> list[str]:
        self.calls.append(term)
        return self.by_prefix.get(term, [])


class _FakeSearch:
    def __init__(self, by_term: dict[str, list[dict]]) -> None:
        self.by_term = by_term

    async def search_apps(self, term: str, country: str = "us", limit: int = 10) -> list[dict]:
        return self.by_term.get(term, [])


@pytest.fixture(autouse=True)
def _clear_hint_cache():
    free.clear_hint_cache()
    yield
    free.clear_hint_cache()


def test_off_title_top_ten_is_an_open_niche():
    supply = classify_supply([_app("Calm"), _app("Headspace")], "box breathing", NOW)
    assert (supply.real_apps, supply.weak_apps) == (0, 0)
    assert opportunity(60, 30, supply.real_apps) == 42


def test_crowded_term_scores_zero():
    results = [_app(f"Box Breathing {i}") for i in range(NICHE_MAX_REAL_APPS + 1)]
    supply = classify_supply(results, "box breathing", NOW)
    assert supply.real_apps == NICHE_MAX_REAL_APPS + 1
    assert opportunity(90, 10, supply.real_apps) == 0


def test_stale_or_underrated_on_title_apps_are_weak():
    results = [
        _app("Box Breathing Old", updated=STALE),
        _app("Box Breathing Tiny", ratings=REAL_APP_MIN_RATINGS - 1),
        _app("Box Breathing Undated", updated=None),
        _app("Box Breathing Naive", updated=(NOW - timedelta(days=30)).replace(tzinfo=None).isoformat()),
    ]
    supply = classify_supply(results, "box breathing", NOW)
    assert (supply.real_apps, supply.weak_apps) == (1, 3)


def test_scan_expands_dedupes_caps_and_drops_unsearched(monkeypatch):
    monkeypatch.setattr(niche, "MAX_NICHE_TERMS", 3)
    hints = _FakeHints({
        "b": ["breathing", "breathing app"],
        "breathing": ["breathing", "breathing app", "breathing timer"],
        "breathing a": ["breathing app", "breathing apnea"],
        "breathing t": ["breathing timer", "breathing tracker"],
    })
    search = _FakeSearch({"breathing": [_app("Breathing Pro")] * 3})
    finder = NicheFinder(suggestions=hints, search=search)

    terms = run_async(finder.candidates(["Breathing"], "US"))
    assert terms == ["breathing", "breathing app", "breathing timer"]

    out = run_async(finder.scan(["Breathing"], "US"))
    assert out.scanned == 3
    assert all(n.popularity >= niche.NICHE_MIN_POPULARITY for n in out.niches)
    by_term = {n.term: n for n in out.niches}
    assert by_term["breathing"].opportunity == 0
    assert by_term["breathing app"].real_apps == 0
    assert [n.opportunity for n in out.niches] == sorted((n.opportunity for n in out.niches), reverse=True)


def test_long_tail_covers_every_seed_stops_at_cap_and_asks_each_prefix_once(monkeypatch):
    monkeypatch.setattr(niche, "MAX_NICHE_TERMS", 5)
    hints = _FakeHints({
        "yoga": ["yoga"],
        "yoga a": ["yoga app", "yoga apnea", "yoga a: studio"],
        "tea a": ["tea app"],
        "yoga b": ["yoga ball"],
    })
    finder = NicheFinder(suggestions=hints, search=_FakeSearch({}))

    terms = run_async(finder.candidates(["yoga", "tea"], "US"))

    assert terms == ["yoga", "tea", "yoga app", "yoga apnea", "tea app"]
    assert "yoga b" not in hints.calls
    assert hints.calls.count("yoga") == 1


def test_tool_registered():
    registered = {t.name for t in run_async(mcp.list_tools())}
    assert "keyword_intel_niche_scan" in registered
