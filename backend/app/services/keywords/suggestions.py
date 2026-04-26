"""iTunes Search Hints API integration for keyword suggestions."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class ITunesSuggestionsService:
    """Get keyword suggestions from iTunes Search Hints API."""

    HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"

    async def get_suggestions(self, term: str, locale: str = "en_us") -> list[str]:
        """Get autocomplete suggestions for a search term.

        Args:
            term: Search term.
            locale: iTunes locale (e.g., "en_us", "de_de").

        Returns:
            List of suggested keyword strings.
        """
        if not term or not term.strip():
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    self.HINTS_URL,
                    params={
                        "media": "software",
                        "term": term.strip(),
                        "l": locale,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "iTunes hints API request failed for term=%r locale=%r",
                    term,
                    locale,
                )
                return []

            data = response.json()
            suggestions: list[str] = []
            for item in data.get("hints", []):
                if isinstance(item, dict):
                    value = item.get("term", "")
                    if value:
                        suggestions.append(value)
                elif isinstance(item, str) and item:
                    suggestions.append(item)

            return suggestions
