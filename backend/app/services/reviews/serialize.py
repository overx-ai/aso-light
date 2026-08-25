"""Shared helpers for converting ASC JSON:API review payloads into ``ReviewOut``.

The REST router (``app/api/v1/reviews.py``) and the MCP tools
(``app/mcp/tools/reviews.py``) both consume Apple's review payload shape, so
the JSON-to-Pydantic adapter, the cursor extractor, and the territory →
default-reply-locale table all live here. Keeping the logic in one place
guarantees both surfaces stay in lock-step.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.schemas.review import ReviewOut, ReviewResponseOut

# ASC returns alpha-3 territory codes. Map common ones to a default reply
# locale; everything else falls back to en-US.
TERRITORY_TO_LOCALE: dict[str, str] = {
    "USA": "en-US", "GBR": "en-GB", "AUS": "en-AU", "CAN": "en-CA",
    "NZL": "en-NZ", "IRL": "en-IE",
    "DEU": "de-DE", "AUT": "de-DE", "CHE": "de-DE",
    "FRA": "fr-FR", "BEL": "fr-FR", "LUX": "fr-FR",
    "ESP": "es-ES", "MEX": "es-MX", "ARG": "es-MX", "CHL": "es-MX",
    "COL": "es-MX", "PER": "es-MX",
    "ITA": "it-IT",
    "JPN": "ja-JP",
    "KOR": "ko-KR",
    "CHN": "zh-Hans", "TWN": "zh-Hant", "HKG": "zh-Hant",
    "RUS": "ru-RU",
    "BRA": "pt-BR", "PRT": "pt-PT",
    "NLD": "nl-NL",
    "POL": "pl-PL",
    "TUR": "tr-TR",
    "SWE": "sv-SE", "NOR": "no-NO", "DNK": "da-DK", "FIN": "fi-FI",
    "IDN": "id-ID", "MYS": "ms-MY", "THA": "th-TH", "VNM": "vi-VN",
    "ARE": "ar-SA", "SAU": "ar-SA",
    "ISR": "he-IL",
    "IND": "hi-IN",
    "GRC": "el-GR",
    "CZE": "cs-CZ", "SVK": "sk-SK", "HUN": "hu-HU", "ROU": "ro-RO",
    "UKR": "uk-UA", "BGR": "bg-BG", "HRV": "hr-HR",
}

DEFAULT_REPLY_LOCALE = "en-US"


def territory_to_locale(territory: str | None) -> str:
    """Map an ASC alpha-3 territory code to a sensible default reply locale."""
    if not territory:
        return DEFAULT_REPLY_LOCALE
    return TERRITORY_TO_LOCALE.get(territory.upper(), DEFAULT_REPLY_LOCALE)


def _response_from_included(
    response_id: str, included: list[dict[str, Any]],
) -> ReviewResponseOut | None:
    """Find the ``customerReviewResponses`` row matching ``response_id``."""
    for inc in included:
        if (
            inc.get("type") == "customerReviewResponses"
            and inc.get("id") == response_id
        ):
            attrs = inc.get("attributes") or {}
            return ReviewResponseOut(
                id=inc.get("id", ""),
                body=attrs.get("responseBody") or "",
                last_modified_date=attrs.get("lastModifiedDate"),
                state=attrs.get("state"),
            )
    return None


def serialize_review(
    raw: dict[str, Any], included: list[dict[str, Any]] | None = None,
) -> ReviewOut:
    """Convert an ASC JSON:API review payload (+ included responses) → ReviewOut."""
    attrs = raw.get("attributes") or {}
    response: ReviewResponseOut | None = None

    rel = (raw.get("relationships") or {}).get("response", {}).get("data")
    if rel and included:
        response = _response_from_included(rel.get("id", ""), included)

    return ReviewOut(
        id=raw.get("id", ""),
        rating=int(attrs.get("rating") or 0),
        title=attrs.get("title"),
        body=attrs.get("body"),
        territory=attrs.get("territory"),
        reviewer_nickname=attrs.get("reviewerNickname"),
        created_date=attrs.get("createdDate"),
        response=response,
    )


def extract_cursor(payload: dict[str, Any]) -> str | None:
    """Pull the decoded ``cursor`` value out of Apple's pagination ``next`` link.

    ``parse_qs`` percent-decodes the token; a still-encoded value fed back
    into the next request's ``cursor`` param would get double-encoded by
    httpx.
    """
    next_link = (payload.get("links") or {}).get("next")
    if not next_link:
        return None
    values = parse_qs(urlsplit(next_link).query).get("cursor")
    return values[0] if values else None
