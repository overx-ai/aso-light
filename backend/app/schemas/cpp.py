"""Schemas for Custom Product Pages (CPP) + their screenshot sets.

Mirrors the request/response shapes consumed by ``app/mcp/tools/cpp.py`` and
produced by ``app/services/asc/cpp.py``. CPP resources follow the App Store
Connect resource hierarchy:

    appCustomProductPages
      -> appCustomProductPageVersions
        -> appCustomProductPageLocalizations
          -> appScreenshotSets -> appScreenshots

Screenshots reuse the standard set/asset model also used by the live
(default) product page, so :class:`ScreenshotSet` / :class:`Screenshot`
serve both the CPP fetch and the later default-page compare path.
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
# Custom Product Page
# ------------------------------------------------------------------


class CPPCreateIn(BaseModel):
    """Request body for creating a Custom Product Page."""

    name: str = Field(..., min_length=1, max_length=100)
    visible: bool = True


class CPPResponse(BaseModel):
    """A single Custom Product Page (``appCustomProductPages`` resource)."""

    id: str
    name: str | None = None
    visible: bool | None = None


class CPPListResponse(BaseModel):
    """Response wrapper for a list of Custom Product Pages."""

    items: list[CPPResponse] = Field(default_factory=list)


class CPPFromUploadResponse(BaseModel):
    """Result of creating a Custom Product Page from an uploaded screenshot set."""

    cpp_id: str
    name: str | None = None
    uploaded_count: int


# ------------------------------------------------------------------
# CPP Version
# ------------------------------------------------------------------


class CPPVersion(BaseModel):
    """A CPP version (``appCustomProductPageVersions`` resource).

    ``version`` and ``state`` are read-only attributes assigned by Apple;
    ``deep_link`` is the optional in-app deep link the page opens to.
    """

    id: str
    version: str | None = None
    state: str | None = None
    deep_link: str | None = None


class CPPVersionListResponse(BaseModel):
    """Response wrapper for a list of CPP versions."""

    items: list[CPPVersion] = Field(default_factory=list)


# ------------------------------------------------------------------
# CPP Localization
# ------------------------------------------------------------------


class CPPLocalization(BaseModel):
    """A CPP localization (``appCustomProductPageLocalizations`` resource)."""

    id: str
    locale: str | None = None
    promotional_text: str | None = None


class CPPLocalizationListResponse(BaseModel):
    """Response wrapper for a list of CPP localizations."""

    items: list[CPPLocalization] = Field(default_factory=list)


class CPPEnsureLocalizationResponse(BaseModel):
    """Resolved (found-or-created) localization for a CPP + locale.

    ``localization_id`` is the ``appCustomProductPageLocalizations`` id that
    ``cpp.upload_screenshot`` requires; ``version_id`` is the editable draft
    version it lives under.
    """

    cpp_id: str
    version_id: str
    localization_id: str
    locale: str


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
