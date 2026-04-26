"""iTunes Search API integration for checking app rankings."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class ITunesSearchService:
    """Check app rankings using the iTunes Search API."""

    SEARCH_URL = "https://itunes.apple.com/search"

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
