"""Daily app-idea digest (spec 021): chart topics -> niche scan -> Markdown cards -> message."""

from __future__ import annotations

import asyncio
import fcntl
import itertools
import json
import logging
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.schemas.idea_digest import DigestState, IdeaOut
from app.schemas.keyword_intel import NicheOut, NicheScanOut
from app.services.keyword_intel.free import DEFAULT_STOREFRONT, _Search, storefront_stopwords
from app.services.keyword_intel.niche import NICHE_MAX_REAL_APPS
from app.services.keyword_intel.tokenize import core_words, ngrams

logger = logging.getLogger(__name__)

TOPICS_PER_DAY = 3
IDEAS_PER_DAY = 5
IDEAS_PER_TOPIC = 2
# Above this the top 10 is giants serving the term under generic titles.
IDEA_MAX_MEDIAN_RATINGS = 5000
# Apple answers bursts of /search with an IP-wide 403 that outlasts the burst and also
# hits rank tracking, so the digest goes slowly and stops once refused.
SEARCH_INTERVAL_SECONDS = 6.0
SEARCH_MAX_CONSECUTIVE_FAILURES = 5
WEEKLY_TOP = 10
WEEK_DAYS = 7
WEEKLY_WEEKDAY = 0
TOPIC_REUSE_DAYS = 60
TOPIC_MIN_DEVELOPERS = 2
TOPIC_MAX_WORDS = 3
IDEA_MIN_OPPORTUNITY = 25
CHART_SIZE = 100
CHART_URL = "https://itunes.apple.com/{country}/rss/topfreeapplications/limit={limit}/genre={genre}/json"
STATUS_NEW = "new"
STATUS_REJECTED = "rejected"
STATE_FILE = "state.json"
LOCK_FILE = ".lock"
MAX_UNSENT = 7
SLUG_MAX_BYTES = 120
RIVALS_HEADING = "## Who ranks today"
PLAIN_FIELDS = frozenset({"status", "created", "updated"})

# Games are left out: their search demand is title-driven.
GENRES: dict[int, str] = {
    6013: "Health & Fitness", 6007: "Productivity", 6002: "Utilities", 6012: "Lifestyle",
    6015: "Finance", 6017: "Education", 6023: "Food & Drink", 6020: "Medical",
    6027: "Graphics & Design", 6008: "Photo & Video", 6003: "Travel", 6000: "Business",
    6006: "Reference", 6004: "Sports", 6011: "Music", 6010: "Navigation",
    6001: "Weather", 6018: "Books", 6024: "Shopping", 6005: "Social Networking",
}


class IdeaDeliveryError(Exception):
    pass


class IdeaDigestBusyError(Exception):
    pass


class _Scanner(Protocol):
    async def scan(self, seeds: Sequence[str], storefront: str) -> NicheScanOut: ...


class _Charts(Protocol):
    async def top(self, genre: int, country: str) -> list[tuple[str, str]]: ...


class _Notifier(Protocol):
    async def send(self, text: str) -> None: ...


class PacedSearch:
    def __init__(self, inner: _Search, interval: float = SEARCH_INTERVAL_SECONDS) -> None:
        self._inner = inner
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._failures = 0

    async def search_apps(self, term: str, country: str = "us", limit: int = 10) -> list[dict]:
        async with self._lock:
            if self._failures >= SEARCH_MAX_CONSECUTIVE_FAILURES:
                return []
            await asyncio.sleep(max(0.0, self._next - time.monotonic()))
            self._next = time.monotonic() + self._interval
            try:
                results = await self._inner.search_apps(term, country, limit=limit)
            except ValueError:
                # A block page can arrive as a 200 with a non-JSON body.
                results = []
            self._failures = 0 if results else self._failures + 1
            return results


class AppleCharts:
    async def top(self, genre: int, country: str) -> list[tuple[str, str]]:
        """(title, developer) pairs; empty when the feed is down."""
        url = CHART_URL.format(country=country.lower(), limit=CHART_SIZE, genre=genre)
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                entries = response.json()["feed"].get("entry", [])
                return [(e["im:name"]["label"], e["im:artist"]["label"]) for e in entries]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            logger.warning("App Store chart unavailable genre=%s country=%s", genre, country)
            return []


class HttpNotifier:
    def __init__(self, url: str) -> None:
        self._url = url

    async def send(self, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                (await client.post(self._url, json={"text": text})).raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise IdeaDeliveryError("digest message was not delivered") from exc


def idea_key(term: str) -> str:
    return " ".join(sorted(set(core_words(term))))


def printable(text: str) -> str:
    """Hints and titles are third-party text headed for one-line frontmatter and Telegram."""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def chart_topics(apps: Iterable[tuple[str, str]], storefront: str) -> list[str]:
    stopwords = storefront_stopwords(storefront)
    developers: dict[str, set[str]] = defaultdict(set)
    for title, developer in apps:
        for gram in ngrams(title, TOPIC_MAX_WORDS, stopwords):
            if " " in gram:
                developers[gram].add(developer)
    shared = {g: devs for g, devs in developers.items() if len(devs) >= TOPIC_MIN_DEVELOPERS}
    # "minded dating" only ever occurs inside "open minded dating": keep the whole phrase.
    whole = {
        gram: devs for gram, devs in shared.items()
        if not any(f" {gram} " in f" {other} " and other != gram and devs == shared[other] for other in shared)
    }
    return sorted(whole, key=lambda g: (-len(whole[g]), g))


class IdeaStore:
    """Cards are the source of truth: Studio rewrites `status:` in place on a decision."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def ideas(self) -> list[IdeaOut]:
        out = [idea for path in self.root.glob("*.md") if (idea := self._read(path))]
        return sorted(out, key=lambda i: (i.created, i.opportunity), reverse=True)

    def recent(self, today: date, days: int) -> list[IdeaOut]:
        since = today - timedelta(days=days - 1)
        return [i for i in self.ideas() if i.created >= since]

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / LOCK_FILE).open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise IdeaDigestBusyError("another idea digest run is in progress") from exc
            yield

    def _write(self, path: Path, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)

    def _free_path(self, idea: IdeaOut) -> Path:
        """Never an existing card: it may already hold a decision."""
        slug = re.sub(r"\W+", "-", idea.term).strip("-").encode()[:SLUG_MAX_BYTES].decode(errors="ignore") or "idea"
        stem = f"{idea.created.isoformat()}-{slug}"
        names = itertools.chain([stem], (f"{stem}-{n}" for n in itertools.count(2)))
        return next(path for name in names if not (path := self.root / f"{name}.md").exists())

    def add(self, idea: IdeaOut) -> None:
        meta = idea.model_dump(mode="json", exclude={"top_titles"})
        # JSON scalars are valid YAML, so quotes or a leading "-" in a term cannot break the block.
        lines = [
            f"{key}: {value if key in PLAIN_FIELDS else json.dumps(value, ensure_ascii=False)}"
            for key, value in meta.items()
        ]
        rivals = "\n".join(f"- {t}" for t in idea.top_titles) or "- none"
        body = (
            f"# {idea.term}\n\n{idea_line(idea)}\n\nCategory: {idea.category}. Topic: {idea.topic}. "
            f"Median ratings in the top 10: {idea.median_ratings}.\n\n{RIVALS_HEADING}\n\n{rivals}\n"
        )
        self._write(self._free_path(idea), "---\n" + "\n".join(lines) + f"\n---\n\n{body}")

    def _read(self, path: Path) -> IdeaOut | None:
        try:
            text = path.read_text(encoding="utf-8")
            block = text.split("---\n", 2)[1]
            meta: dict[str, Any] = {}
            for line in block.splitlines():
                key, _, raw = line.partition(": ")
                try:
                    meta[key] = json.loads(raw)
                except ValueError:
                    meta[key] = raw.strip()
            titles = [ln[2:] for ln in text.partition(RIVALS_HEADING)[2].splitlines() if ln.startswith("- ")]
            return IdeaOut.model_validate({**meta, "top_titles": titles})
        except (OSError, IndexError, ValueError):
            logger.warning("Skipping unreadable idea card %s", path.name)
            return None

    @property
    def _state_path(self) -> Path:
        return self.root / STATE_FILE

    def state(self) -> DigestState:
        path = self._state_path
        if not path.exists():
            return DigestState()
        try:
            return DigestState.model_validate_json(path.read_bytes())
        except ValueError:
            logger.warning("Digest state is corrupt, set aside and started fresh")
            path.replace(path.with_suffix(".corrupt"))
            return DigestState()

    def save_state(self, state: DigestState) -> None:
        self._write(self._state_path, state.model_dump_json(indent=1))


def idea_line(idea: IdeaOut) -> str:
    return (
        f"opportunity {idea.opportunity}, popularity {idea.popularity}, difficulty {idea.difficulty}, "
        f"{idea.real_apps} real apps"
    )


def _numbered(ideas: Sequence[IdeaOut]) -> list[str]:
    return [f"{n}. {i.term}: {idea_line(i)}" for n, i in enumerate(ideas, 1)]


def render(
    today: date, ideas: Sequence[IdeaOut], topics: Sequence[str], scanned: int, skipped: int, weekly: Sequence[IdeaOut],
) -> str:
    lines = [f"App ideas {today.isoformat()}", f"Topics: {', '.join(topics) or 'none'} ({scanned} searches checked)"]
    if skipped:
        lines.append(f"{skipped} lookups got no results (Apple may be refusing them), so this is incomplete.")
    if not topics:
        lines.append("Apple's charts were unavailable, nothing scanned.")
    elif not ideas:
        lines.append("No new ideas today.")
    lines += _numbered(ideas)
    if weekly:
        lines += ["", "Top of the week", *_numbered(weekly)]
    return "\n".join(lines)


@dataclass(slots=True)
class DigestResult:
    message: str
    ideas: list[IdeaOut] = field(default_factory=list)
    rescanned: bool = True


@dataclass(slots=True)
class _ScanTotals:
    found: list[tuple[NicheOut, str, str]] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    scanned: int = 0
    skipped: int = 0

    def add(self, out: NicheScanOut, topic: str, category: str) -> None:
        self.scanned += out.scanned
        self.skipped += out.skipped
        self.found += [(n, topic, category) for n in out.niches]
        if out.skipped and not out.niches:
            self.refused.append(topic)


class IdeaDigest:
    def __init__(
        self, store: IdeaStore, scanner: _Scanner, charts: _Charts, notifier: _Notifier,
        storefront: str = DEFAULT_STOREFRONT,
    ) -> None:
        self._store = store
        self._scanner = scanner
        self._charts = charts
        self._notifier = notifier
        self._storefront = storefront

    async def run(self, today: date) -> DigestResult:
        with self._store.exclusive():
            state = self._store.state()
            if state.last_run == today:
                result = DigestResult("", rescanned=False)
            else:
                result = await self._scan(today, state)
                state.unsent = [*state.unsent, result.message][-MAX_UNSENT:]
                self._store.save_state(state)
            result.message = "\n\n".join(state.unsent)
            await self._deliver(state)
            return result

    async def _deliver(self, state: DigestState) -> None:
        while state.unsent:
            await self._notifier.send(state.unsent[0])
            state.unsent.pop(0)
            self._store.save_state(state)

    async def _scan(self, today: date, state: DigestState) -> DigestResult:
        topics = await self._topics(today, state)
        shown = {idea_key(i.term) for i in self._store.ideas()}
        totals = _ScanTotals()
        # One scan per topic, so each gets the full term cap instead of sharing it.
        for topic, category in topics:
            totals.add(await self._scanner.scan([topic], self._storefront), topic, category)
        for idea in self._select(today, totals.found, shown):
            self._store.add(idea)
        ideas = self._unannounced(today, state)
        self._settle(today, state, topics, totals, ideas)
        weekly = self._weekly(today) if today.weekday() == WEEKLY_WEEKDAY else []
        message = render(today, ideas, [t for t, _ in topics], totals.scanned, totals.skipped, weekly)
        return DigestResult(message, ideas)

    def _unannounced(self, today: date, state: DigestState) -> list[IdeaOut]:
        """Read back from the cards, so a run that died before saving state loses nothing."""
        since = state.last_run or today - timedelta(days=1)
        return [i for i in self._store.ideas() if i.created > since and i.status == STATUS_NEW]

    @staticmethod
    def _settle(
        today: date, state: DigestState, topics: Sequence[tuple[str, str]], totals: _ScanTotals,
        ideas: Sequence[IdeaOut],
    ) -> None:
        """An outage uses up neither the topics it hit nor, when nothing got through, the day."""
        # A topic that expands to nothing is possible; all of them at once is a hints outage.
        hints_down = not totals.found and totals.scanned <= len(topics)
        scanned = [] if hints_down else [t for t, _ in topics if t not in totals.refused]
        state.topics.update(dict.fromkeys(scanned, today))
        if scanned or ideas:
            state.last_run = today

    def _select(
        self, today: date, found: Sequence[tuple[NicheOut, str, str]], shown: set[str],
    ) -> list[IdeaOut]:
        ideas: list[IdeaOut] = []
        per_topic: Counter[str] = Counter()
        for niche, topic, category in sorted(found, key=lambda f: -f[0].opportunity):
            key = idea_key(niche.term)
            if (
                key in shown
                or niche.opportunity < IDEA_MIN_OPPORTUNITY
                or niche.real_apps > NICHE_MAX_REAL_APPS
                or niche.median_ratings > IDEA_MAX_MEDIAN_RATINGS
                or per_topic[topic] >= IDEAS_PER_TOPIC
            ):
                continue
            shown.add(key)
            per_topic[topic] += 1
            ideas.append(IdeaOut(
                **niche.model_dump(exclude={"term", "top_titles"}), term=printable(niche.term),
                top_titles=[printable(t) for t in niche.top_titles], status=STATUS_NEW, created=today, updated=today,
                topic=topic, category=category, storefront=self._storefront,
            ))
            if len(ideas) == IDEAS_PER_DAY:
                break
        return ideas

    def _weekly(self, today: date) -> list[IdeaOut]:
        live = [i for i in self._store.recent(today, WEEK_DAYS) if i.status != STATUS_REJECTED]
        return sorted(live, key=lambda i: -i.opportunity)[:WEEKLY_TOP]

    async def _topics(self, today: date, state: DigestState) -> list[tuple[str, str]]:
        cutoff = today - timedelta(days=TOPIC_REUSE_DAYS)
        state.topics = {t: d for t, d in state.topics.items() if d > cutoff}
        genres = list(GENRES.items())
        start = today.toordinal() * TOPICS_PER_DAY
        picked: dict[str, str] = {}
        for offset in range(len(genres)):
            genre, category = genres[(start + offset) % len(genres)]
            for topic in chart_topics(await self._charts.top(genre, self._storefront), self._storefront):
                if topic not in state.topics and topic not in picked:
                    picked[topic] = category
                    break
            if len(picked) == TOPICS_PER_DAY:
                break
        return list(picked.items())
