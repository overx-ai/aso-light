"""Niche finder (spec 020): searched terms whose iTunes top 10 holds no real app."""

from __future__ import annotations

import asyncio
import itertools
import math
import re
import statistics
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.schemas.keyword_intel import NicheOut, NicheScanOut
from app.services.keyword_intel.free import (
    FreeKeywordIntel,
    _default_services,
    _Search,
    _Suggestions,
    rating_count,
    results_difficulty,
)
from app.services.keyword_intel.tokenize import core_words, covers_core, segments, words
from app.services.keywords.concurrency import gather_bounded
from app.services.keywords.tracker import REFRESH_CONCURRENCY

MAX_NICHE_TERMS = 60
NICHE_MIN_POPULARITY = 20
NICHE_MAX_REAL_APPS = 2
REAL_APP_MIN_RATINGS = 1000
STALE_DAYS = 540
GIANT_MIN_RATINGS = 50_000
# One generic shared word ("ai") does not make an app a rival.
GIANT_MIN_SHARED_WORDS = 2
# One product to a searcher. Difficulty still compares the exact words, as Apple ranks them.
PRODUCT_SYNONYMS = dict.fromkeys(("maker", "makers", "generator", "generators", "creator", "creators"), "maker")
LONG_TAIL_LETTERS = string.ascii_lowercase
TOP_TITLES_SHOWN = 5
# Narrower than tokenize's segment break: people do search "bed & breakfast" and "24/7 gym".
_TITLE_PUNCTUATION = re.compile(r"[:|,·•–—]|\s-\s")
# "sleep sounds by ambient" is an app attributed to its maker; "color by number" is a search.
ATTRIBUTION_WORD = "by"
BY_PHRASE_ENDINGS = frozenset({
    "age", "date", "day", "hour", "letter", "letters", "location", "me", "name", "number", "numbers",
    "photo", "picture", "side", "step", "voice", "week", "year", "zip",
})
# Hints list developer names too ("breathing app, llc").
COMPANY_SUFFIXES = frozenset({"llc", "inc", "ltd", "limited", "gmbh", "corp"})


@dataclass(slots=True)
class Supply:
    real_apps: int
    weak_apps: int
    giants: int
    median_ratings: int


def _updated(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def canonical(tokens: Iterable[str]) -> set[str]:
    return {PRODUCT_SYNONYMS.get(w, w) for w in tokens}


def product_words(text: str | None) -> set[str]:
    return canonical(core_words(text))


def is_giant(ratings: int, title: set[str], core: set[str]) -> bool:
    needed = max(min(GIANT_MIN_SHARED_WORDS, len(core)), math.ceil(len(core) / 2))
    return bool(core) and ratings >= GIANT_MIN_RATINGS and len(core & title) >= needed


def is_app_name(term: str, titles: Sequence[str]) -> bool:
    """Hints complete to app names too ("breathing zone" for "Breathing Zone: Breathwork"),
    and Apple ranks the named app first. Other titles carrying the same words among more mean the
    first one is merely descriptive ("Sleep Sounds") and the search is a real one; a title of
    exactly these words is only a twin."""
    core = core_words(term)
    if not core or not titles:
        return False
    first, *others = titles
    named = any(core_words(" ".join(tokens)) == core for tokens in (words(first), *segments(first)[:1]))
    return named and not any(covers_core(title, core) and core_words(title) != core for title in others)


def classify_supply(results: Sequence[dict], term: str, now: datetime) -> Supply:
    cutoff = now - timedelta(days=STALE_DAYS)
    core = product_words(term)
    real = weak = giants = 0
    for r in results:
        # Modifiers stay in: "best app" is made of nothing else.
        title = canonical(words(r.get("name")))
        giants += is_giant(rating_count(r), title, core)
        if not core or not core <= title:
            continue
        updated = _updated(r.get("updated_at"))
        if rating_count(r) >= REAL_APP_MIN_RATINGS and updated and updated >= cutoff:
            real += 1
        else:
            weak += 1
    counts = [rating_count(r) for r in results]
    return Supply(real, weak, giants, round(statistics.median(counts)) if counts else 0)


def is_attributed(tokens: Sequence[str]) -> bool:
    return any(
        word == ATTRIBUTION_WORD and following not in BY_PHRASE_ENDINGS
        for word, following in itertools.pairwise(tokens)
    )


def is_title_like(hint: str) -> bool:
    """App and developer names among hints; a typed search has no title punctuation."""
    tokens = words(hint)
    return (
        bool(_TITLE_PUNCTUATION.search(hint))
        or is_attributed(tokens)
        or not COMPANY_SUFFIXES.isdisjoint(tokens[-1:])
    )


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


def _normalize(seeds: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(s.casefold().strip() for s in seeds))


class NicheFinder:
    def __init__(self, suggestions: _Suggestions | None = None, search: _Search | None = None) -> None:
        default_suggestions, default_search = _default_services()
        self._suggestions = _MemoSuggestions(suggestions or default_suggestions)
        self._intel = FreeKeywordIntel(suggestions=self._suggestions, search=search or default_search)

    async def _long_tail(self, probe: str, country: str) -> list[str]:
        hints = (h.casefold().strip() for h in await self._suggestions.get_suggestions(probe, country))
        return [t for t in hints if t.startswith(probe) and not is_title_like(t)]

    async def candidates(self, seeds: Sequence[str], storefront: str) -> list[str]:
        normalized = _normalize(seeds)
        expansion = await self._intel.seed_expansion(normalized, storefront, exclude=set(normalized))
        terms = dict.fromkeys([*normalized, *(t for t in expansion if not is_title_like(t))])
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
        asked = set(_normalize(seeds))
        now = datetime.now(timezone.utc)
        skipped = 0

        async def one(term: str) -> NicheOut | None:
            nonlocal skipped
            popularity = await self._intel.popularity(term, storefront)
            if popularity < NICHE_MIN_POPULARITY:
                return None
            results = await self._intel.top_results(term, storefront)
            # search_apps swallows HTTP errors as []; a searched term never has zero results,
            # so scoring one would rank an outage as a wide-open niche.
            if not results:
                skipped += 1
                return None
            difficulty, titles = results_difficulty(results, term)
            if term not in asked and is_app_name(term, titles):
                return None
            supply = classify_supply(results, term, now)
            return NicheOut(
                term=term,
                popularity=popularity,
                difficulty=difficulty,
                real_apps=supply.real_apps,
                weak_apps=supply.weak_apps,
                giants=supply.giants,
                median_ratings=supply.median_ratings,
                opportunity=opportunity(popularity, difficulty, supply.real_apps),
                top_titles=titles[:TOP_TITLES_SHOWN],
            )

        scored = [n for n in await gather_bounded(terms, one, concurrency=REFRESH_CONCURRENCY) if n]
        scored.sort(key=lambda n: (-n.opportunity, -n.popularity))
        return NicheScanOut(
            storefront=storefront, seeds=list(seeds), scanned=len(terms), skipped=skipped, niches=scored,
        )
