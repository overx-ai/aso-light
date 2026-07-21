"""Shared schemas for App Store screenshot sets + their assets.

The ``appScreenshotSets`` -> ``appScreenshots`` model is identical whether the
parent is a Custom Product Page localization, a live App Store version
localization, or a Product Page Optimization (App Store Version Experiment)
treatment localization. These models — and the ``screenshotDisplayType``
validation used to reject typo'd device families at the API boundary — live
here so every consumer (``app.schemas.cpp``, ``app.schemas.experiment``) shares
one definition.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Upload guard-rails for a from-upload screenshot set. Apple caps a single
# screenshot at 10 MB and a set at 10 screenshots; we enforce the same so a
# client cannot buffer unbounded bytes in memory before the (serial) ASC upload.
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOT_FILES = 10


# ------------------------------------------------------------------
# Apple ``screenshotDisplayType`` (device family) — known values
# ------------------------------------------------------------------
#
# Apple's documented ``ScreenshotDisplayType`` enum. Used to reject typo'd /
# free-text device families at the API boundary before they reach ASC (which
# would otherwise return an opaque 502). Update when Apple adds device families
# — mirror the device list offered by the frontend ``DISPLAY_TYPES``.
SCREENSHOT_DISPLAY_TYPES: frozenset[str] = frozenset(
    {
        "APP_IPHONE_67", "APP_IPHONE_65", "APP_IPHONE_61", "APP_IPHONE_58",
        "APP_IPHONE_55", "APP_IPHONE_47", "APP_IPHONE_40", "APP_IPHONE_35",
        "APP_IPAD_PRO_3GEN_129", "APP_IPAD_PRO_3GEN_11", "APP_IPAD_PRO_129",
        "APP_IPAD_105", "APP_IPAD_97",
        "APP_DESKTOP", "APP_APPLE_TV", "APP_APPLE_VISION_PRO",
        "APP_WATCH_ULTRA", "APP_WATCH_SERIES_10", "APP_WATCH_SERIES_7",
        "APP_WATCH_SERIES_4", "APP_WATCH_SERIES_3",
        "IMESSAGE_APP_IPHONE_67", "IMESSAGE_APP_IPHONE_65",
        "IMESSAGE_APP_IPHONE_61", "IMESSAGE_APP_IPHONE_58",
        "IMESSAGE_APP_IPHONE_55", "IMESSAGE_APP_IPHONE_47",
        "IMESSAGE_APP_IPHONE_40",
        "IMESSAGE_APP_IPAD_PRO_3GEN_129", "IMESSAGE_APP_IPAD_PRO_3GEN_11",
        "IMESSAGE_APP_IPAD_PRO_129", "IMESSAGE_APP_IPAD_105",
        "IMESSAGE_APP_IPAD_97",
    }
)


def is_valid_display_type(display_type: str) -> bool:
    """Return whether ``display_type`` is a known Apple ``screenshotDisplayType``."""
    return display_type in SCREENSHOT_DISPLAY_TYPES


# ------------------------------------------------------------------
# Screenshots
# ------------------------------------------------------------------


class Screenshot(BaseModel):
    """A single App Store screenshot (``appScreenshots`` resource).

    ``source_url`` is the rendered CDN URL built from the asset's
    ``imageAsset.templateUrl`` (``{w}``/``{h}``/``{f}`` substituted); it is
    ``None`` while the source upload is still pending.
    """

    id: str
    file_name: str | None = None
    display_type: str | None = None
    source_url: str | None = None


class ScreenshotSet(BaseModel):
    """A screenshot set (``appScreenshotSets`` resource) plus its assets.

    ``display_type`` is Apple's ``screenshotDisplayType`` (e.g.
    ``APP_IPHONE_67``), which identifies the device family the set targets.
    """

    id: str
    display_type: str | None = None
    screenshots: list[Screenshot] = Field(default_factory=list)


class ScreenshotSetListResponse(BaseModel):
    """Response wrapper for a list of screenshot sets."""

    items: list[ScreenshotSet] = Field(default_factory=list)
