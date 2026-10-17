"""Schemas for app preview videos synced from an export directory (spec 017)."""
from __future__ import annotations

from app.schemas.screenshots import ScreenshotSyncResult

# Apple's ``PreviewType`` enum (App Store Connect API, checked 2026-10-17).
PREVIEW_TYPES: frozenset[str] = frozenset(
    {
        "IPHONE_67", "IPHONE_65", "IPHONE_61", "IPHONE_58", "IPHONE_55",
        "IPHONE_47", "IPHONE_40", "IPHONE_35",
        "IPAD_PRO_3GEN_129", "IPAD_PRO_3GEN_11", "IPAD_PRO_129", "IPAD_105",
        "IPAD_97",
        "DESKTOP", "APPLE_TV", "APPLE_VISION_PRO",
    }
)


class PreviewSyncResult(ScreenshotSyncResult):
    """The screenshot sync's result, for app previews.

    Every ``display_type`` (rows, ``untouched``, ``inventory``) holds Apple's
    ``previewType``. ``applied`` stays false while any row is an ``error``;
    after an apply, ``count`` and ``inventory`` are the read-back.
    ``missing_families`` is always empty: Apple does not require previews."""
