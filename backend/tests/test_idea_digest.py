from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

import pytest

from app.mcp.consent import READ_ONLY
from app.schemas.idea_digest import IdeaOut
from app.schemas.keyword_intel import NicheOut, NicheScanOut
from app.services.idea_digest import (
    IDEA_MAX_MEDIAN_RATINGS,
    IDEA_MIN_OPPORTUNITY,
    IDEAS_PER_DAY,
    SEARCH_MAX_CONSECUTIVE_FAILURES,
    TOPICS_PER_DAY,
    WEEKLY_TOP,
    IdeaDeliveryError,
    IdeaDigest,
    IdeaDigestBusyError,
    IdeaStore,
    PacedSearch,
    chart_topics,
    is_rights_bound,
)
from tests._async_harness import run_async

MONDAY = date(2026, 11, 23)
TUESDAY = date(2026, 11, 24)


def _niche(term: str, opportunity: int = 40, real_apps: int = 0) -> NicheOut:
    return NicheOut(
        term=term, popularity=60, difficulty=30, real_apps=real_apps, weak_apps=0,
        median_ratings=10, opportunity=opportunity, top_titles=["Rival: One"],
    )


class _Scanner:
    def __init__(self, niches: list[NicheOut]) -> None:
        self.niches = niches
        self.seeds: list[str] = []
        self.skipped = 0

    async def scan(self, seeds, storefront):
        self.seeds += seeds
        # Each topic owns every third niche, so the per-topic cap is exercised.
        mine = self.niches[(len(self.seeds) - 1) % TOPICS_PER_DAY::TOPICS_PER_DAY]
        return NicheScanOut(
            storefront=storefront, seeds=list(seeds), scanned=10, skipped=self.skipped, niches=mine,
        )


class _Charts:
    def __init__(self, up: bool = True) -> None:
        self.up = up

    async def top(self, genre, country):
        if not self.up:
            return []
        return [(f"Alpha{genre} Tracker", "A"), (f"Alpha{genre} Tracker Pro", "B")]


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.down = False

    async def send(self, text):
        if self.down:
            raise IdeaDeliveryError("down")
        self.sent.append(text)


@pytest.fixture
def store(tmp_path):
    return IdeaStore(tmp_path / "ideas")


def _digest(store, niches, charts=None):
    scanner, notifier = _Scanner(niches), _Notifier()
    return IdeaDigest(store, scanner, charts or _Charts(), notifier), scanner, notifier


def test_topics_need_two_developers():
    apps = [("Meal Planner", "A"), ("Meal Planner Pro", "B"), ("Google Docs", "G"), ("Google Docs Lite", "G")]
    assert chart_topics(apps, "US") == ["meal planner"]


def test_topic_is_the_whole_shared_phrase():
    apps = [("Open Minded Dating", "A"), ("Zed: Open Minded Dating", "B")]
    assert chart_topics(apps, "US") == ["open minded dating"]


def test_rights_bound_searches():
    assert all(is_rights_bound(t) for t in [
        "live sports", "live sports streaming free", "live football tv", "watch movies offline",
        "music downloads",
    ])
    assert not any(is_rights_bound(t) for t in [
        "live football scores", "live sports schedules", "movie maker", "tv remote", "music video", "anime drawing",
        "live weather",
    ])


def test_rights_bound_topics_are_not_scanned():
    apps = [
        ("Watch Movies Offline", "A"), ("Watch Movies Offline Pro", "B"),
        ("Meal Planner", "A"), ("Meal Planner Pro", "B"),
    ]
    assert chart_topics(apps, "US") == ["meal planner"]


def test_giants_rights_bound_terms_and_synonyms_are_not_ideas(store):
    owned = _niche("owned", 95)
    owned.giants = 1
    digest, _, notifier = _digest(store, [
        owned, _niche("live sports streaming", 94), _niche("logo maker", 60), _niche("logo generator", 59),
    ])
    run_async(digest.run(TUESDAY))
    assert [i.term for i in store.ideas()] == ["logo maker"]


def test_refused_lookups_are_reported(store):
    digest, scanner, notifier = _digest(store, [])
    scanner.skipped = 2
    run_async(digest.run(TUESDAY))
    assert "6 lookups got no results" in notifier.sent[0]


def test_paced_search_spaces_concurrent_calls():
    class Inner:
        at: list[float] = []

        async def search_apps(self, term, country="us", limit=10):
            self.at.append(time.monotonic())
            return [{"name": term}]

    async def go():
        paced = PacedSearch(Inner(), interval=0.05)
        await asyncio.gather(*(paced.search_apps(t) for t in "abc"))

    run_async(go())
    assert Inner.at[2] - Inner.at[0] >= 0.09


def test_paced_search_stops_calling_once_refused():
    class Refusing:
        calls = 0

        async def search_apps(self, term, country="us", limit=10):
            Refusing.calls += 1
            return []

    async def go():
        paced = PacedSearch(Refusing(), interval=0)
        for n in range(SEARCH_MAX_CONSECUTIVE_FAILURES + 4):
            await paced.search_apps(str(n))

    run_async(go())
    assert Refusing.calls == SEARCH_MAX_CONSECUTIVE_FAILURES


def test_daily_run_keeps_the_best_new_ideas_and_round_trips_cards(store):
    giants = _niche("giants", 95)
    giants.median_ratings = IDEA_MAX_MEDIAN_RATINGS + 1
    # Columns are topics: A holds three good ideas, so its weakest is over the per-topic cap.
    niches = [
        _niche('- odd "one": x', 99), _niche("b1", 50), _niche("c1", 45),
        _niche("a2", 60), _niche("b2", 49), _niche("c2", 44),
        _niche("a3", 59), giants, _niche("crowded", 90, 3),
        _niche("weak", IDEA_MIN_OPPORTUNITY - 1),
    ]
    digest, scanner, notifier = _digest(store, niches)

    run_async(digest.run(TUESDAY))

    assert len(set(scanner.seeds)) == TOPICS_PER_DAY
    terms = [i.term for i in store.ideas()]
    assert terms == ['- odd "one": x', "a2", "b1", "b2", "c1"]
    assert len(terms) == IDEAS_PER_DAY
    assert store.ideas()[0].top_titles == ["Rival: One"]
    assert "Top of the week" not in notifier.sent[0]


def test_ideas_and_topics_never_repeat_and_same_day_rerun_is_silent(store):
    digest, scanner, notifier = _digest(store, [_niche("app for kids")])
    run_async(digest.run(TUESDAY))
    assert run_async(digest.run(TUESDAY)).rescanned is False
    assert len(scanner.seeds) == TOPICS_PER_DAY and len(notifier.sent) == 1

    scanner.niches = [_niche("kids")]
    run_async(digest.run(TUESDAY + timedelta(days=1)))
    assert len(store.ideas()) == 1
    assert len(set(scanner.seeds)) == 2 * TOPICS_PER_DAY
    assert "No new ideas today" in notifier.sent[1]


def test_failed_send_is_delivered_later_without_a_rescan(store):
    digest, scanner, notifier = _digest(store, [_niche("kids")])
    notifier.down = True
    with pytest.raises(IdeaDeliveryError):
        run_async(digest.run(TUESDAY))
    notifier.down = False

    run_async(digest.run(TUESDAY))

    assert len(scanner.seeds) == TOPICS_PER_DAY
    assert "kids" in notifier.sent[0]


def test_monday_top_ten_covers_the_week_and_skips_rejected(store):
    def card(term: str, opportunity: int, days_ago: int, status: str = "new") -> None:
        day = MONDAY - timedelta(days=days_ago)
        store.add(IdeaOut(
            **_niche(term, opportunity).model_dump(), status=status, created=day, updated=day,
            topic="t", category="c", storefront="US",
        ))

    for n in range(WEEKLY_TOP + 1):
        card(f"live{n}", 40 + n, 3)
    card("toolate", 98, 7)
    card("turned down", 99, 1)
    path = next(store.root.glob("*turned-down*"))
    path.write_text(path.read_text().replace("status: new", "status: rejected"))
    digest, _, notifier = _digest(store, [])

    run_async(digest.run(MONDAY))

    weekly = notifier.sent[0].partition("Top of the week")[2]
    assert weekly.count("opportunity") == WEEKLY_TOP
    assert "live10" in weekly and "live0:" not in weekly
    assert "toolate" not in weekly and "turned down" not in weekly


def test_chart_outage_is_reported_not_shown_as_no_ideas(store):
    digest, scanner, notifier = _digest(store, [_niche("kids")], _Charts(up=False))
    run_async(digest.run(TUESDAY))
    assert scanner.seeds == []
    assert "charts were unavailable" in notifier.sent[0]


def test_refusal_uses_up_neither_the_day_nor_the_topics(store):
    digest, scanner, notifier = _digest(store, [])
    scanner.skipped = 5
    run_async(digest.run(TUESDAY))

    scanner.skipped, scanner.niches = 0, [_niche("kids")]
    run_async(digest.run(TUESDAY))

    assert scanner.seeds[:TOPICS_PER_DAY] == scanner.seeds[TOPICS_PER_DAY:]
    assert "kids" in notifier.sent[1]


def test_cards_of_a_run_that_died_are_announced_by_the_next(store, monkeypatch):
    digest, _, notifier = _digest(store, [_niche("kids")])
    with monkeypatch.context() as broken:
        broken.setattr(store, "save_state", lambda state: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            run_async(digest.run(TUESDAY))
    run_async(digest.run(TUESDAY))
    assert "1. kids:" in notifier.sent[0]
    assert len(store.ideas()) == 1


def test_corrupt_state_starts_fresh_and_is_set_aside(store):
    store.root.mkdir()
    (store.root / "state.json").write_text("{oops")
    digest, _, notifier = _digest(store, [_niche("kids")])
    run_async(digest.run(TUESDAY))
    assert "kids" in notifier.sent[0]
    assert (store.root / "state.corrupt").exists()


def test_second_concurrent_run_is_refused(store):
    digest, scanner, _ = _digest(store, [_niche("kids")])
    with store.exclusive(), pytest.raises(IdeaDigestBusyError):
        run_async(digest.run(TUESDAY))
    assert scanner.seeds == []


def test_same_slug_never_overwrites_and_hostile_text_stays_in_the_folder(store):
    hostile = "../../x\u2028status: approved\n" + "\u044f" * 300
    digest, _, notifier = _digest(store, [_niche(hostile), _niche("tip: calc"), _niche("tip, calc pro")])
    run_async(digest.run(TUESDAY))
    first = store.ideas()[0]
    store.add(first)
    assert len(store.ideas()) == 4
    assert {i.status for i in store.ideas()} == {"new"}
    assert not any(c in notifier.sent[0] for c in "\u2028\r")


def test_tool_is_read_only():
    assert "idea_digest_list" in READ_ONLY
