"""Free keyword intel from Apple's public search hints and iTunes search (spec 014).

Scores are a relative ranking, not search volume — Apple publishes volume only via ASA.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.stopwords import language_of, stopwords_for_locales
from app.data.storefronts import DEFAULT_COUNTRY
from app.models.app import App
from app.models.keyword import Keyword, KeywordTracking
from app.models.keyword_intel import KeywordIntelCache
from app.models.metadata import AppMetadataLocalization
from app.schemas.keyword_intel import (
    FreeScoreOut,
    FreeTermScore,
    MinedCandidate,
    PackFieldsOut,
    VariantPickOut,
)
from app.services.keyword_intel.asa_recommendations import (
    SOURCE_NAME as ASA_RECOMMENDATIONS_SOURCE,
)
from app.services.keyword_intel.base import KeywordIntel, KeywordIntelProvider
from app.services.keyword_intel.packer import (
    PackCandidate,
    bulk_preview_payload,
    pack_storefront,
)
from app.services.keyword_intel.tokenize import contains_phrase, ngrams, words
from app.services.keywords.concurrency import gather_bounded
from app.services.keywords.cross_localization import (
    get_indexed_locales_for_territory,
    get_territories_for_locale,
)
from app.services.keywords.itunes_search import ITunesSearchService
from app.services.keywords.suggestions import ITunesSuggestionsService
from app.services.keywords.tracker import REFRESH_CONCURRENCY

logger = logging.getLogger(__name__)

SOURCE_NAME = "free_hints"
POPULARITY_SOURCE_ASA = "asa"
POPULARITY_SOURCE_HINTS = "hints"
# ASA pins "no estimate" to 5; at or below it the hints score is the better signal.
ASA_POPULARITY_FLOOR = 5

# Hint calls are the budget driver (up to len(term) per term), so terms per call are capped.
MAX_FREE_TERMS = 25
MAX_PACK_TERMS = 10
MAX_PACK_LOCALES = 4
MAX_VARIANT_STOREFRONTS = 2
# Hard bound on hint calls per term: tracked keywords may be up to 255 chars, and a match
# this deep would score near the floor anyway.
MAX_PREFIX_PROBES = 20
DIFFICULTY_TOP_N = 10
MINING_MAX_NGRAM = 2
LISTING_MAX_NGRAM = 3
MAX_MINED_CANDIDATES = 30
SEED_EXPANSION_TOP = 3
SEED_PREFIX_LEN = 2
MAX_EXPANSION_TERMS = 10

RATING_COUNT_CEILING = 1_000_000
RATING_WEIGHT = 0.6

HINT_CACHE_TTL_SECONDS = 24 * 3600
HINT_CACHE_MAX_ENTRIES = 5000

DEFAULT_STOREFRONT = DEFAULT_COUNTRY.upper()


class _Suggestions(Protocol):
    async def get_suggestions(self, term: str, country: str = ...) -> list[str]: ...


class _Search(Protocol):
    async def search_apps(self, term: str, country: str = ..., limit: int = ...) -> list[dict]: ...


VariantSource = Callable[[str, str], Awaitable[list[str]]]

_hint_cache: dict[tuple[str, str], tuple[float, int]] = {}


def clear_hint_cache() -> None:
    _hint_cache.clear()


def _default_services() -> tuple[_Suggestions, _Search]:
    return ITunesSuggestionsService(), ITunesSearchService()


def prefix_depth_score(prefix_len: int, term_len: int, *, position: int, list_size: int) -> int:
    """Shorter matching prefix always outranks a longer one; list position only orders
    within one prefix length, because its contribution stays below one depth step."""
    rank_fraction = 1 - position / max(list_size, 1)
    return max(1, round(100 * ((term_len - prefix_len) + rank_fraction) / term_len))


def organic_difficulty(rating_counts: Sequence[int], titles: Sequence[str], term: str) -> int:
    median = statistics.median(rating_counts) if rating_counts else 0
    rating_part = min(1.0, math.log10(1 + median) / math.log10(1 + RATING_COUNT_CEILING))
    title_share = (
        sum(contains_phrase(title, term) for title in titles) / len(titles) if titles else 0.0
    )
    return round(100 * (RATING_WEIGHT * rating_part + (1 - RATING_WEIGHT) * title_share))


def blend_popularity(*, asa: int | None, hints: int) -> tuple[int, str]:
    if asa is not None and asa > ASA_POPULARITY_FLOOR:
        return asa, POPULARITY_SOURCE_ASA
    return hints, POPULARITY_SOURCE_HINTS


@dataclass(slots=True)
class VariantPick:
    best: str
    score: int
    alternatives: list[str] = field(default_factory=list)
    not_searched_here: bool = False


def pick_variant(scored: dict[str, int]) -> VariantPick:
    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    best, score = ranked[0]
    return VariantPick(
        best=best,
        score=score,
        alternatives=[term for term, _ in ranked[1:]],
        not_searched_here=score == 0,
    )


def mine_titles(
    titles: Iterable[str],
    stopwords: frozenset[str],
    brand: str | None,
    limit: int = MAX_MINED_CANDIDATES,
) -> list[MinedCandidate]:
    counts: Counter[str] = Counter()
    for title in titles:
        counts.update(set(ngrams(title, MINING_MAX_NGRAM, stopwords, brand)))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [MinedCandidate(term=t, count=c) for t, c in ranked]


def extract_listing_terms(
    *,
    name: str | None,
    subtitle: str | None,
    keywords: str | None,
    description: str | None,
    stopwords: frozenset[str],
    brand: str | None,
) -> set[str]:
    terms: set[str] = set()
    for text in (name, subtitle, keywords, description):
        terms.update(ngrams(text, LISTING_MAX_NGRAM, stopwords, brand))
    return terms


def brand_token(app_name: str | None) -> str | None:
    tokens = words(app_name)
    return tokens[0] if tokens else None


def storefront_for_locale(locale: str) -> str:
    """The storefront a locale is primarily written for: its region subtag when that
    storefront indexes it, else a storefront whose first indexed locale it is."""
    territories = get_territories_for_locale(locale)
    region = locale.replace("_", "-").split("-")[-1].upper()
    if region in territories:
        return region
    primary = [t for t in territories if get_indexed_locales_for_territory(t)[:1] == [locale]]
    return (primary or territories or [DEFAULT_STOREFRONT])[0]


def storefront_stopwords(storefront: str) -> frozenset[str]:
    return stopwords_for_locales(get_indexed_locales_for_territory(storefront))


class FreeKeywordIntel:
    def __init__(self, suggestions: _Suggestions | None = None, search: _Search | None = None) -> None:
        default_suggestions, default_search = _default_services()
        self._suggestions = suggestions or default_suggestions
        self._search = search or default_search

    async def popularity(self, term: str, storefront: str) -> int:
        normalized = term.casefold().strip()
        key = (normalized, storefront.upper())
        cached = _hint_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < HINT_CACHE_TTL_SECONDS:
            return cached[1]

        score = 0
        saw_hints = False
        for prefix_len in range(1, min(len(normalized), MAX_PREFIX_PROBES) + 1):
            if normalized[prefix_len - 1].isspace():
                continue
            hints = [
                h.casefold().strip()
                for h in await self._suggestions.get_suggestions(
                    normalized[:prefix_len], storefront.lower(),
                )
            ]
            saw_hints = saw_hints or bool(hints)
            if normalized in hints:
                score = prefix_depth_score(
                    prefix_len, len(normalized),
                    position=hints.index(normalized), list_size=len(hints),
                )
                break

        # get_suggestions swallows HTTP errors as []; a walk that saw no hints at all is
        # most likely an outage, and caching its 0 would mark terms unsearched for a day.
        if saw_hints:
            _hint_cache.pop(key, None)
            while len(_hint_cache) >= HINT_CACHE_MAX_ENTRIES:
                del _hint_cache[next(iter(_hint_cache))]
            _hint_cache[key] = (time.monotonic(), score)
        return score

    async def difficulty(self, term: str, storefront: str) -> tuple[int, list[str]]:
        results = (await self._search.search_apps(
            term, storefront.lower(), limit=DIFFICULTY_TOP_N,
        ))[:DIFFICULTY_TOP_N]
        titles = [r.get("name") or "" for r in results]
        counts = [int(r.get("rating_count") or 0) for r in results]
        return organic_difficulty(counts, titles, term), titles

    async def score_terms(
        self,
        terms: Sequence[str],
        storefront: str,
        *,
        asa_popularity: dict[str, int] | None = None,
        brand: str | None = None,
        extra_titles: Iterable[str] = (),
    ) -> FreeScoreOut:
        asa = asa_popularity or {}

        async def one(term: str) -> tuple[FreeTermScore, list[str]]:
            hints = await self.popularity(term, storefront)
            difficulty, titles = await self.difficulty(term, storefront)
            asa_value = asa.get(term.casefold())
            popularity, source = blend_popularity(asa=asa_value, hints=hints)
            return FreeTermScore(
                term=term,
                storefront=storefront,
                popularity=popularity,
                popularity_source=source,
                hints_popularity=hints,
                asa_popularity=asa_value,
                difficulty=difficulty,
            ), titles

        results = await gather_bounded(terms, one, concurrency=REFRESH_CONCURRENCY)
        all_titles = [t for _, titles in results for t in titles]
        all_titles.extend(extra_titles)
        return FreeScoreOut(
            storefront=storefront,
            scores=[score for score, _ in results],
            candidates=mine_titles(all_titles, storefront_stopwords(storefront), brand),
        )

    async def pick_best_variant(self, variants: Sequence[str], locale: str, storefront: str) -> VariantPick:
        storefronts = list(dict.fromkeys([storefront, *get_territories_for_locale(locale)]))
        storefronts = storefronts[:MAX_VARIANT_STOREFRONTS]

        async def mean_score(variant: str) -> int:
            scores = [await self.popularity(variant, s) for s in storefronts]
            return round(statistics.mean(scores))

        scores = await gather_bounded(variants, mean_score, concurrency=REFRESH_CONCURRENCY)
        return pick_variant(dict(zip(variants, scores)))

    async def seed_expansion(
        self, seeds: Sequence[str], storefront: str, *, exclude: set[str],
    ) -> dict[str, int]:
        """Hints for short prefixes of validated terms; each hint's popularity comes from
        its own position in that list, so expansion costs one hint call per seed."""
        found: dict[str, int] = {}
        for seed in seeds:
            prefix = seed.casefold()[:SEED_PREFIX_LEN]
            hints = await self._suggestions.get_suggestions(prefix, storefront.lower())
            for position, hint in enumerate(hints):
                term = hint.casefold().strip()
                if len(term) < len(prefix) or term in exclude or term in found:
                    continue
                found[term] = prefix_depth_score(
                    len(prefix), len(term), position=position, list_size=len(hints),
                )
                if len(found) >= MAX_EXPANSION_TERMS:
                    return found
        return found

    async def build_pack(
        self,
        *,
        terms: Sequence[str],
        storefront: str,
        source_locale: str,
        locales: Sequence[str],
        variants_for: VariantSource,
        names_by_locale: dict[str, str | None],
        subtitles_by_locale: dict[str, str | None],
    ) -> PackFieldsOut:
        picks_out: list[VariantPickOut] = []
        expansion_out: dict[str, list[str]] = {}
        candidates_by_locale: dict[str, list[PackCandidate]] = {}

        for locale in locales:
            same_language = language_of(locale) == language_of(source_locale)
            # Sequential: variants_for may share the caller's DB session (translation cache).
            variants = [
                [term] if same_language else (await variants_for(term, locale) or [term])
                for term in terms
            ]

            async def pick(term_variants: list[str], locale: str = locale) -> VariantPick:
                return await self.pick_best_variant(term_variants, locale, storefront)

            picks = await gather_bounded(variants, pick, concurrency=REFRESH_CONCURRENCY)
            picks_out.extend(
                VariantPickOut(
                    source_term=term, locale=locale, best=p.best, score=p.score,
                    alternatives=p.alternatives, not_searched_here=p.not_searched_here,
                )
                for term, p in zip(terms, picks)
            )
            validated = sorted(
                (p for p in picks if not p.not_searched_here), key=lambda p: -p.score,
            )
            popularity = {
                p.best: await self.popularity(p.best, storefront) for p in validated
            }
            expansion = await self.seed_expansion(
                [p.best for p in validated[:SEED_EXPANSION_TOP]], storefront,
                exclude=set(popularity),
            )
            expansion_out[locale] = list(expansion)
            popularity.update(expansion)

            async def candidate(item: tuple[str, int]) -> PackCandidate:
                term, term_popularity = item
                difficulty, _ = await self.difficulty(term, storefront)
                return PackCandidate(term=term, popularity=term_popularity, difficulty=difficulty)

            candidates_by_locale[locale] = await gather_bounded(
                list(popularity.items()), candidate, concurrency=REFRESH_CONCURRENCY,
            )

        fields = pack_storefront(
            candidates_by_locale,
            names_by_locale=names_by_locale,
            subtitles_by_locale=subtitles_by_locale,
        )
        return PackFieldsOut(
            storefront=storefront,
            picks=picks_out,
            expansion=expansion_out,
            values_by_locale=fields,
            bulk_preview=bulk_preview_payload(fields),
        )


async def asa_popularity_by_term(session: AsyncSession, app_id: int, storefront: str) -> dict[str, int]:
    rows = await session.execute(
        select(KeywordIntelCache.keyword, KeywordIntelCache.volume_score).where(
            KeywordIntelCache.app_id == app_id,
            KeywordIntelCache.source == ASA_RECOMMENDATIONS_SOURCE,
            KeywordIntelCache.locale == storefront,
        )
    )
    return {kw.casefold(): score for kw, score in rows.all() if score is not None}


async def listing_terms_from_lookup(
    track_id: str,
    storefront: str,
    *,
    stopwords: frozenset[str],
    brand: str | None,
    include_description: bool = True,
) -> set[str]:
    """iTunes lookup carries no subtitle or keyword field: name + description only."""
    found = await ITunesSearchService().lookup_apps([track_id], storefront.lower())
    if not found:
        return set()
    return extract_listing_terms(
        name=found[0].get("trackName"), subtitle=None, keywords=None,
        description=found[0].get("description") if include_description else None,
        stopwords=stopwords, brand=brand,
    )


async def own_listing_terms(
    session: AsyncSession, app: App, locale: str, *, include_description: bool = True,
) -> set[str]:
    """The ``using`` set: the app's own listing as n-grams, from the ASC snapshot when synced."""
    stopwords = stopwords_for_locales([locale])
    brand = brand_token(app.name)
    rows = (await session.execute(
        select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app.id,
            AppMetadataLocalization.locale == locale,
        )
    )).scalars().all()
    by_kind = {r.kind: r for r in rows}
    if not by_kind:
        return await listing_terms_from_lookup(
            app.asc_app_id, storefront_for_locale(locale), stopwords=stopwords, brand=brand,
            include_description=include_description,
        )
    info, version = by_kind.get("app_info"), by_kind.get("version")
    return extract_listing_terms(
        name=info.name if info else None,
        subtitle=info.subtitle if info else None,
        keywords=version.keywords if version else None,
        description=version.description if version and include_description else None,
        stopwords=stopwords,
        brand=brand,
    )


class FreeIntelProvider(KeywordIntelProvider):
    """Scores the app's tracked keywords in each keyword locale's primary storefront."""

    name = SOURCE_NAME

    def __init__(self) -> None:
        self._hints_by_keyword_id: dict[int, int] = {}

    async def fetch(self, *, app_id: int, session: AsyncSession, **_: Any) -> list[KeywordIntel]:
        app = (await session.execute(select(App).where(App.id == app_id))).scalar_one_or_none()
        if app is None:
            return []
        keywords = (await session.execute(
            select(Keyword)
            .join(KeywordTracking, KeywordTracking.keyword_id == Keyword.id)
            .where(KeywordTracking.app_id == app_id)
            .order_by(KeywordTracking.created_at.desc())
            .limit(MAX_FREE_TERMS)
        )).scalars().all()

        by_storefront: dict[str, list[Keyword]] = {}
        for keyword in keywords:
            by_storefront.setdefault(storefront_for_locale(keyword.locale), []).append(keyword)

        intel = FreeKeywordIntel()
        brand = brand_token(app.name)
        rows: list[KeywordIntel] = []
        for storefront, group in by_storefront.items():
            result = await intel.score_terms(
                [k.text for k in group], storefront,
                asa_popularity=await asa_popularity_by_term(session, app_id, storefront),
                brand=brand,
            )
            for keyword, score in zip(group, result.scores):
                self._hints_by_keyword_id[keyword.id] = score.hints_popularity
                rows.append(KeywordIntel(
                    keyword=keyword.text,
                    locale=storefront,
                    source=SOURCE_NAME,
                    volume_score=score.popularity,
                    difficulty_score=score.difficulty,
                    raw_score=score.hints_popularity,
                    popularity_source=score.popularity_source,
                ))
        return rows

    async def after_upsert(self, session: AsyncSession) -> None:
        """``Keyword`` rows are shared across apps, so they get the app-independent hints score."""
        if not self._hints_by_keyword_id:
            return
        now = datetime.now(timezone.utc)
        keywords = (await session.execute(
            select(Keyword).where(Keyword.id.in_(self._hints_by_keyword_id))
        )).scalars().all()
        for keyword in keywords:
            keyword.popularity = self._hints_by_keyword_id[keyword.id]
            keyword.popularity_updated_at = now
        await session.flush()
