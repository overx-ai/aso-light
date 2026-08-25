"""iTunes Search Hints API integration for keyword suggestions."""

from __future__ import annotations

import logging
import plistlib
from typing import Any

import httpx

from app.data.storefronts import (
    DEFAULT_COUNTRY,
    normalize_country,
    storefront_header,
)
from app.services.keywords.throttle import itunes_throttle

logger = logging.getLogger(__name__)


class ITunesSuggestionsService:
    """Get keyword suggestions from iTunes Search Hints API."""

    HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"

    async def get_suggestions(
        self,
        term: str,
        country: str = DEFAULT_COUNTRY,
        *,
        locale: str | None = None,
    ) -> list[str]:
        """Get autocomplete suggestions for a search term.

        The ``X-Apple-Store-Front`` header is what selects the storefront (and
        with it the language of the hints). Apple answers a header-less request
        with an empty ``<array/>`` and HTTP 200, so it is not optional. The old
        ``l=`` query param is not sent — the endpoint ignores it.

        **Precedence: an explicit ``country`` always wins.** The deprecated
        ``locale`` is consulted only when ``country`` is left at its ``"us"``
        default, so ``country="de", locale="en_us"`` resolves to ``de``: the
        replacement parameter can never be silently overridden by the parameter
        it replaced (which is what a REST client sending both — the router
        forwards both — would otherwise get). A disagreement is logged.

        Args:
            term: Search term.
            country: Two-letter country code (e.g., "us", "de"), matching
                :meth:`ITunesSearchService.search_apps`. Locale-shaped values
                ("en_us", "de_de") are accepted and reduced to their country.
            locale: Deprecated alias for ``country``, kept for one release so
                stored client calls keep working. ``en_us`` → ``us``.

        Returns:
            List of suggested keyword strings.
        """
        selected = country
        if locale is not None:
            if normalize_country(country) == DEFAULT_COUNTRY:
                selected = locale
            elif normalize_country(locale) != normalize_country(country):
                logger.warning(
                    "keywords suggestions got country=%r and the deprecated "
                    "locale=%r, which disagree; honouring country=%r",
                    country,
                    locale,
                    country,
                )
        header, resolved_country = storefront_header(selected)

        if not term or not term.strip():
            return []

        await itunes_throttle()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    self.HINTS_URL,
                    params={
                        "clientApplication": "Software",
                        "media": "software",
                        "term": term.strip(),
                    },
                    headers={"X-Apple-Store-Front": header},
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "iTunes hints API request failed for term=%r country=%r",
                    term,
                    resolved_country,
                )
                return []

            data = self._parse_response(response)
            suggestions: list[str] = []
            for item in data.get("hints", []):
                if isinstance(item, dict):
                    value = item.get("term", "")
                    if value:
                        suggestions.append(value)
                elif isinstance(item, str) and item:
                    suggestions.append(item)

            if not suggestions:
                # A silent [] is indistinguishable from "Apple has no hints" —
                # which is precisely how the missing-header bug survived.
                logger.warning(
                    "iTunes hints API returned no suggestions for term=%r country=%r "
                    "(storefront=%s)",
                    term,
                    resolved_country,
                    header,
                )

            return suggestions

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        """Decode the hints response, handling both JSON and Apple's plist XML."""
        body = response.content
        if not body:
            return {}
        try:
            return response.json()
        except ValueError:
            pass
        try:
            parsed = plistlib.loads(body)
        except (plistlib.InvalidFileException, ValueError, TypeError):
            logger.warning("iTunes hints API returned unparseable body (%d bytes)", len(body))
            return {}
        return parsed if isinstance(parsed, dict) else {}
