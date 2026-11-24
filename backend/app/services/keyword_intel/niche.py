"""Niche finder (spec 020): searched terms whose iTunes top 10 holds no real app."""

from __future__ import annotations

import asyncio
import statistics
import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.schemas.keyword_intel import NicheOut, NicheScanOut
from app.services.keyword_intel.free import (
    APP_TITLE_MARK,
    FreeKeywordIntel,
    _default_services,
    _Search,
    _Suggestions,
    rating_count,
    results_difficulty,
)
from app.services.keyword_intel.tokenize import contains_phrase
from app.services.keywords.concurrency import gather_bounded
from app.services.keywords.tracker import REFRESH_CONCURRENCY

MAX_NICHE_TERMS = 30
NICHE_MIN_POPULARITY = 20
NICHE_MAX_REAL_APPS = 2
REAL_APP_MIN_RATINGS = 1000
STALE_DAYS = 540
LONG_TAIL_LETTERS = string.ascii_lowercase
TOP_TITLES_SHOWN = 5


@dataclass(slots=True)
class Supply:
    real_apps: int
    weak_apps: int
    median_ratings: int


def _updated(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_supply(results: Sequence[dict], term: str, now: datetime) -> Supply:
    cutoff = now - timedelta(days=STALE_DAYS)
    real = weak = 0
    for r in results:
        if not contains_phrase(r.get("name"), term):
            continue
        updated = _updated(r.get("updated_at"))
        if rating_count(r) >= REAL_APP_MIN_RATINGS and updated and updated >= cutoff:
            real += 1
        else:
            weak += 1
    counts = [rating_count(r) for r in results]
    return Supply(real, weak, round(statistics.median(counts)) if counts else 0)


def opportunity(popularity: int, difficulty: int, real_apps: int) -> int:
    if real_apps > NICHE_MAX_REAL_APPS:
        return 0
    return round(popularity * (100 - difficulty) / 100)


class _MemoSuggestions:
    """Popularity walks of terms sharing a seed repeat the same prefixes; one scan asks once.
    Stores the in-flight task, not the result, so concurrent walks share one request."""

    def __init__(self, inner: _Suggestions) -> None:
        self._inner = inner
        self._seen: dict[tuple[str, str], asyncio.Future[list[str]]] = {}

    async def get_suggestions(self, term: str, country: str = "us") -> list[str]:
        key = (term, country)
        if key not in self._seen:
            self._seen[key] = asyncio.ensure_future(self._inner.get_suggestions(term, country))
        return await self._seen[key]


class NicheFinder:
    def __init__(self, suggestions: _Suggestions | None = None, search: _Search | None = None) -> None:
        default_suggestions, default_search = _default_services()
        self._suggestions = _MemoSuggestions(suggestions or default_suggestions)
        self._intel = FreeKeywordIntel(suggestions=self._suggestions, search=search or default_search)

    async def _long_tail(self, probe: str, country: str) -> list[str]:
        hints = (h.casefold().strip() for h in await self._suggestions.get_suggestions(probe, country))
        return [t for t in hints if t.startswith(probe) and APP_TITLE_MARK not in t]

    async def candidates(self, seeds: Sequence[str], storefront: str) -> list[str]:
        normalized = list(dict.fromkeys(s.casefold().strip() for s in seeds))
        expansion = await self._intel.seed_expansion(normalized, storefront, exclude=set(normalized))
        terms = dict.fromkeys([*normalized, *expansion])
        # Letter-major across seeds so every seed gets a long tail, and probing stops at
        # the cap instead of spending 26 hint calls per seed on terms that get cut.
        probes = (f"{seed} {letter}" for letter in LONG_TAIL_LETTERS for seed in normalized)
        for probe in probes:
            if len(terms) >= MAX_NICHE_TERMS:
                break
            terms.update(dict.fromkeys(await self._long_tail(probe, storefront.lower())))
        return list(terms)[:MAX_NICHE_TERMS]

    async def scan(self, seeds: Sequence[str], storefront: str) -> NicheScanOut:
        terms = await self.candidates(seeds, storefront)
        now = datetime.now(timezone.utc)

        async def one(term: str) -> NicheOut | None:
            popularity = await self._intel.popularity(term, storefront)
            if popularity < NICHE_MIN_POPULARITY:
                return None
            results = await self._intel.top_results(term, storefront)
            difficulty, titles = results_difficulty(results, term)
            supply = classify_supply(results, term, now)
            return NicheOut(
                term=term,
                popularity=popularity,
                difficulty=difficulty,
                real_apps=supply.real_apps,
                weak_apps=supply.weak_apps,
                median_ratings=supply.median_ratings,
                opportunity=opportunity(popularity, difficulty, supply.real_apps),
                top_titles=titles[:TOP_TITLES_SHOWN],
            )

        scored = [n for n in await gather_bounded(terms, one, concurrency=REFRESH_CONCURRENCY) if n]
        scored.sort(key=lambda n: (-n.opportunity, -n.popularity))
        return NicheScanOut(storefront=storefront, seeds=list(seeds), scanned=len(terms), niches=scored)
