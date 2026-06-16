"""Shared, non-HTTP helpers for App Clash (iTunes side-by-side comparison).

Both the REST router (``app/api/v1/clash.py``) and the MCP tool
(``app/mcp/tools/clash.py``) map iTunes Lookup rows into ``ClashRow``s the
same way; this module is the single source of truth for that mapping so the
two layers cannot drift. Pure data shaping — no DB, no HTTP, no ownership.
"""

from __future__ import annotations

from typing import Any

from app.schemas.clash import ClashRow

DESCRIPTION_EXCERPT_LEN = 280


def file_size_mb(raw_size: Any) -> float | None:
    """Convert an iTunes ``fileSizeBytes`` value to MB, or ``None`` if absent
    or unparseable."""
    if not isinstance(raw_size, (int, str)):
        return None
    try:
        return round(int(raw_size) / (1024 * 1024), 1)
    except (TypeError, ValueError):
        return None


def description_excerpt(description: str | None) -> str | None:
    """Trim a long App Store description to a fixed-length excerpt."""
    if not description:
        return None
    if len(description) <= DESCRIPTION_EXCERPT_LEN:
        return description
    return description[:DESCRIPTION_EXCERPT_LEN].rstrip() + "…"


def build_row(
    raw: dict[str, Any] | None,
    *,
    is_self: bool,
    asc_app_id: str,
    fallback_name: str | None,
    fallback_bundle: str | None,
) -> ClashRow:
    """Build a ``ClashRow`` from an iTunes lookup result, falling back to the
    locally-known fields when the storefront has no record for the id."""
    if not raw:
        return ClashRow(
            track_id=asc_app_id,
            is_self=is_self,
            name=fallback_name,
            bundle_id=fallback_bundle,
        )
    return ClashRow(
        track_id=str(raw.get("trackId") or ""),
        is_self=is_self,
        name=raw.get("trackName") or fallback_name,
        # iTunes lookup doesn't return subtitles for storefront listings.
        subtitle=None,
        seller=raw.get("sellerName"),
        primary_genre=raw.get("primaryGenreName"),
        average_rating=raw.get("averageUserRating"),
        rating_count=raw.get("userRatingCount"),
        release_date=raw.get("releaseDate"),
        version=raw.get("version"),
        file_size_mb=file_size_mb(raw.get("fileSizeBytes")),
        price=raw.get("price"),
        currency=raw.get("currency"),
        formatted_price=raw.get("formattedPrice"),
        icon_url=raw.get("artworkUrl100"),
        bundle_id=raw.get("bundleId") or fallback_bundle,
        description_excerpt=description_excerpt(raw.get("description")),
    )
