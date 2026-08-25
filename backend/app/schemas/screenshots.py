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

import base64
import binascii

from pydantic import BaseModel, Field

# Upload guard-rails for a from-upload screenshot set. Apple caps a single
# screenshot at 10 MB and a set at 10 screenshots; we enforce the same so a
# client cannot buffer unbounded bytes in memory before the (serial) ASC upload.
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOT_FILES = 10


def decode_screenshot_payload(
    file_base64: str,
    *,
    max_bytes: int = MAX_SCREENSHOT_BYTES,
) -> bytes:
    """Decode + bound a base64 screenshot payload from a caller.

    The single decode shared by every screenshot upload surface (main listing,
    CPP, PPO treatment). It exists because those three had drifted: only one of
    them bounded the decoded size, so the others would buffer an arbitrarily
    large caller-supplied payload in memory — twice over, since the base64
    string is still alive — before ASC ever saw a byte.

    Raises:
        ValueError: on malformed base64, an empty payload, or one over
            ``max_bytes``. Callers translate it into their own error shape
            (``ToolError`` / HTTP 4xx); the message is safe to surface.
    """
    # Bound the ENCODED length first: base64 inflates by 4/3, so an oversized
    # payload is refused without ever materializing the decoded copy.
    if len(file_base64) > (max_bytes // 3 + 1) * 4 + 8:
        raise ValueError(f"Screenshot payload exceeds the {max_bytes}-byte cap.")
    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64 payload: {exc}") from exc
    if not file_bytes:
        raise ValueError("Decoded screenshot payload is empty")
    if len(file_bytes) > max_bytes:
        raise ValueError(
            f"Screenshot is {len(file_bytes)} bytes; the cap is {max_bytes}."
        )
    return file_bytes


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

    ``state`` / ``errors`` mirror Apple's ``assetDeliveryState`` and are only
    populated by callers that asked for it (the main-listing tools); they stay
    ``None`` / empty on the CPP and PPO paths.
    """

    id: str
    file_name: str | None = None
    display_type: str | None = None
    source_url: str | None = None
    state: str | None = None
    errors: list[str] = Field(default_factory=list)


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


# ------------------------------------------------------------------
# Main product-page inventory (per locale x display type)
# ------------------------------------------------------------------


class DisplayTypeScreenshotStatus(BaseModel):
    """One locale's screenshots for one device family.

    ``count`` excludes assets Apple marked ``FAILED`` — those occupy a slot in
    the set but are not a shipped screenshot, and treating them as present is
    exactly how a version reaches submit-time rejection looking complete.
    """

    display_type: str
    set_id: str | None = None
    count: int = 0
    expected: int = 0
    missing: int = 0
    complete: bool = True
    failed: list[str] = Field(default_factory=list)
    screenshots: list[Screenshot] = Field(default_factory=list)


class LocaleScreenshotStatus(BaseModel):
    """One locale's screenshot inventory across every device family."""

    locale: str
    localization_id: str
    total: int = 0
    complete: bool = True
    display_types: list[DisplayTypeScreenshotStatus] = Field(default_factory=list)


class ScreenshotGap(BaseModel):
    """A single (locale, display type) shortfall — the repair worklist item."""

    locale: str
    display_type: str
    count: int
    expected: int
    missing: int


class VersionScreenshotInventory(BaseModel):
    """Per locale x display type counts for the editable App Store version.

    This is the resume/repair primitive: after an interrupted bulk upload,
    ``gaps`` is the exact list of (locale, display type) pairs still short,
    with no need to open N locales in the App Store Connect UI.
    """

    app_id: int
    version_id: str
    version_state: str | None = None
    version_string: str | None = None
    locales: list[LocaleScreenshotStatus] = Field(default_factory=list)
    display_types: list[str] = Field(default_factory=list)
    expected_by_display_type: dict[str, int] = Field(default_factory=dict)
    total_screenshots: int = 0
    gaps: list[ScreenshotGap] = Field(default_factory=list)
    complete: bool = True


# ------------------------------------------------------------------
# Main product-page write results
# ------------------------------------------------------------------


class ScreenshotUploadResult(BaseModel):
    """Outcome of a single main-listing screenshot upload.

    ``verified`` is the read-back verdict, not the HTTP verdict: it is only
    ``True`` when Apple reports the asset as ``COMPLETE``. A committed asset
    still being processed comes back ``verified=False`` with ``warning`` set.
    """

    locale: str
    localization_id: str
    display_type: str
    set_id: str
    position: int
    replaced_screenshot_id: str | None = None
    screenshot: Screenshot
    verified: bool = False
    warning: str | None = None


class ScreenshotDeleteResult(BaseModel):
    """Outcome of a main-listing screenshot delete.

    ``deleted_set`` reports whether the (now empty) set was pruned — an empty
    set is a *configured but incomplete* device family, which fails review.
    """

    locale: str
    display_type: str
    set_id: str | None = None
    deleted_screenshot_ids: list[str] = Field(default_factory=list)
    deleted_set: bool = False
    remaining: int = 0
