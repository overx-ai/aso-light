"""iTunes Search API integration for checking app rankings."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class _HasIconAndAscId(Protocol):
    """Minimal duck-type for ``backfill_icons`` — works on ORM rows or any object
    with an ``asc_app_id`` and a writable ``icon_url`` attribute."""

    asc_app_id: str
    icon_url: str | None


class ITunesSearchService:
    """Check app rankings using the iTunes Search API."""

    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"

    async def lookup_apps(
        self,
        track_ids: list[str],
        country: str = "us",
    ) -> list[dict[str, Any]]:
        """Look up rich metadata for one or more iTunes track IDs.

        Returns the raw iTunes lookup payload entries (one per id found).
        Useful keys: trackName, sellerName, primaryGenreName, averageUserRating,
        userRatingCount, releaseDate, version, fileSizeBytes, price, currency,
        artworkUrl100, formattedPrice, description, trackContentRating.
        """
        if not track_ids:
            return []
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(
                    self.LOOKUP_URL,
                    params={
                        "id": ",".join(t for t in track_ids if t),
                        "country": country,
                        "media": "software",
                        "entity": "software",
                        "limit": min(len(track_ids), 200),
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "iTunes lookup failed for ids=%s country=%s",
                    track_ids, country,
                )
                return []
            data = response.json()
            return list(data.get("results") or [])

    # Fallback storefronts tried (in order) when an app isn't found in the
    # primary country. Covers ~80% of single-region launches. Apps in pre-release
    # / "waiting for review" never appear on iTunes Search regardless of country.
    _ICON_FALLBACK_COUNTRIES = ("us", "gb", "de", "jp", "br", "fr", "ru", "in", "cn")

    async def fetch_icon_urls(
        self, track_ids: list[str], country: str = "us",
    ) -> dict[str, str]:
        """Return a ``{asc_app_id: icon_url}`` map for the given track ids.

        Tries ``country`` first, then falls back through major storefronts for
        ids that come back empty (single-region launches). Uses ``artworkUrl512``
        when present so the dashboard card renders crisply on retina; falls back
        through 100 → 60. Missing ids are simply omitted — callers should leave
        the existing ``icon_url`` untouched on miss.
        """
        if not track_ids:
            return {}

        # dict.fromkeys preserves order while de-duplicating the primary country
        # against the fallback list.
        countries = list(dict.fromkeys([country, *self._ICON_FALLBACK_COUNTRIES]))

        out: dict[str, str] = {}
        remaining = [t for t in track_ids if t]

        for c in countries:
            if not remaining:
                break
            for r in await self.lookup_apps(remaining, country=c):
                track_id = str(r.get("trackId", ""))
                if not track_id or track_id in out:
                    continue
                url = (
                    r.get("artworkUrl512")
                    or r.get("artworkUrl100")
                    or r.get("artworkUrl60")
                )
                if url:
                    out[track_id] = url
            remaining = [t for t in remaining if t not in out]
        return out

    async def backfill_icons(self, apps: list[_HasIconAndAscId]) -> int:
        """Set ``icon_url`` on each app in ``apps`` from iTunes Search.

        Mutates rows in place. Returns the number of icons actually filled
        (apps without an ``asc_app_id`` and apps the iTunes lookup can't find
        are silently skipped, leaving any pre-existing ``icon_url`` untouched).
        Caller is responsible for flushing/committing the session.
        """
        ids = [a.asc_app_id for a in apps if a.asc_app_id]
        if not ids:
            return 0
        icon_map = await self.fetch_icon_urls(ids)
        filled = 0
        for a in apps:
            url = icon_map.get(a.asc_app_id)
            if url:
                a.icon_url = url
                filled += 1
        return filled

    async def search_apps(
        self, term: str, country: str = "us", limit: int = 200,
    ) -> list[dict]:
        """Search for apps and return results with their positions.

        Args:
            term: Search term.
            country: Two-letter country code (e.g., "us", "de").
            limit: Maximum number of results (max 200).

        Returns:
            List of dicts with keys: position, app_id, name, bundle_id, icon_url.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(
                    self.SEARCH_URL,
                    params={
                        "term": term.strip(),
                        "country": country,
                        "media": "software",
                        "limit": min(limit, 200),
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "iTunes search API request failed for term=%r country=%r",
                    term,
                    country,
                )
                return []

            data = response.json()
            results: list[dict] = []
            for i, result in enumerate(data.get("results", []), start=1):
                results.append({
                    "position": i,
                    "app_id": str(result.get("trackId", "")),
                    "name": result.get("trackName", ""),
                    "bundle_id": result.get("bundleId", ""),
                    "icon_url": result.get("artworkUrl60", ""),
                })
            return results

    async def get_app_rank(
        self, term: str, app_id: str, country: str = "us",
    ) -> int | None:
        """Get the rank of a specific app for a search term.

        Args:
            term: Search term.
            app_id: iTunes track ID as string.
            country: Two-letter country code.

        Returns:
            Position (1-indexed) or None if not found in top 200.
        """
        results = await self.search_apps(term, country)
        for result in results:
            if result["app_id"] == app_id:
                return result["position"]
        return None
