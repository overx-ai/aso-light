"""Shared review helpers for API and MCP surfaces."""
from __future__ import annotations

from typing import Any

from app.schemas.review import ReviewOut, ReviewResponseOut
from app.services.reviews.draft import classify_review_theme

# ASC returns alpha-3 territory codes. Map common ones to a default reply
# locale; everything else falls back to en-US.
_TERRITORY_TO_LOCALE: dict[str, str] = {
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


def territory_to_locale(territory: str | None) -> str:
    if not territory:
        return "en-US"
    return _TERRITORY_TO_LOCALE.get(territory.upper(), "en-US")


def serialize_review(
    raw: dict[str, Any],
    included: list[dict[str, Any]] | None = None,
) -> ReviewOut:
    """Convert ASC JSON:API review payload + included responses → ReviewOut."""
    attrs = raw.get("attributes") or {}
    response: ReviewResponseOut | None = None

    rel = (raw.get("relationships") or {}).get("response", {}).get("data")
    if rel and included:
        for inc in included:
            if (
                inc.get("type") == "customerReviewResponses"
                and inc.get("id") == rel.get("id")
            ):
                inc_attrs = inc.get("attributes") or {}
                response = ReviewResponseOut(
                    id=inc.get("id", ""),
                    body=inc_attrs.get("responseBody") or "",
                    last_modified_date=inc_attrs.get("lastModifiedDate"),
                    state=inc_attrs.get("state"),
                )
                break

    rating = int(attrs.get("rating") or 0)
    title = attrs.get("title")
    body = attrs.get("body")
    return ReviewOut(
        id=raw.get("id", ""),
        rating=rating,
        title=title,
        body=body,
        territory=attrs.get("territory"),
        reviewer_nickname=attrs.get("reviewerNickname"),
        created_date=attrs.get("createdDate"),
        response=response,
        theme=classify_review_theme(
            review_title=title,
            review_body=body,
            review_rating=rating,
        ),
    )


def extract_cursor(payload: dict[str, Any]) -> str | None:
    next_link = (payload.get("links") or {}).get("next")
    if not next_link or "cursor=" not in next_link:
        return None
    try:
        return next_link.split("cursor=", 1)[1].split("&", 1)[0]
    except IndexError:
        return None
