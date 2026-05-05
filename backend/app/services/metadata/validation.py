"""Field-level validation for App Store metadata.

Apple enforces hard character limits on most metadata fields and rejects
malformed URLs on the marketing/support/privacy fields. We mirror those
limits here so the editor and bulk-fanout flows can fail fast (and show a
useful char-overflow message) instead of bouncing off ASC with a 400.
"""
from __future__ import annotations

from typing import Final
from urllib.parse import urlparse

# Apple's documented limits (2026)
FIELD_CHAR_LIMITS: Final[dict[str, int]] = {
    "name": 30,
    "subtitle": 30,
    "description": 4000,
    "keywords": 100,            # incl. commas
    "promotional_text": 170,
    "whats_new": 4000,
}

URL_FIELDS: Final[set[str]] = {
    "marketing_url",
    "support_url",
    "privacy_policy_url",
}

ALL_FIELDS: Final[set[str]] = set(FIELD_CHAR_LIMITS) | URL_FIELDS


def is_valid_url(value: str) -> bool:
    """Basic URL validation: requires scheme http/https and netloc."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except (ValueError, AttributeError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def char_overflow(field: str, value: str | None) -> int:
    """Returns 0 if within limit (or N/A); else number of chars over."""
    if value is None:
        return 0
    limit = FIELD_CHAR_LIMITS.get(field)
    if limit is None:
        return 0
    overflow = len(value) - limit
    return overflow if overflow > 0 else 0


def validate_field(field: str, value: str | None) -> tuple[bool, str | None]:
    """Returns (is_valid, error_message_or_None).

    Empty/None values are accepted (clearing a field is valid).
    Unknown field names are rejected.
    """
    if field not in ALL_FIELDS:
        return False, f"Unknown metadata field: {field!r}"

    # Clearing a field is always valid.
    if value is None or value == "":
        return True, None

    if not isinstance(value, str):
        return False, f"Field {field!r} must be a string"

    if field in URL_FIELDS:
        if not is_valid_url(value):
            return False, (
                f"Field {field!r} must be an http(s) URL with a host"
            )
        if len(value) > 1024:
            return False, (
                f"Field {field!r} exceeds 1024-char URL limit"
            )
        return True, None

    overflow = char_overflow(field, value)
    if overflow > 0:
        limit = FIELD_CHAR_LIMITS[field]
        return False, (
            f"Field {field!r} is {overflow} char(s) over the {limit}-char limit"
        )
    return True, None
