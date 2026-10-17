"""Schemas for Custom Product Pages (CPP).

Mirrors the request/response shapes consumed by ``app/mcp/tools/cpp.py`` and
produced by ``app/services/asc/cpp.py``. CPP resources follow the App Store
Connect resource hierarchy:

    appCustomProductPages
      -> appCustomProductPageVersions
        -> appCustomProductPageLocalizations
          -> appScreenshotSets -> appScreenshots

Screenshots reuse the standard set/asset model shared with the live default
product page and PPO treatments, so the :class:`Screenshot` / :class:`ScreenshotSet`
models and the ``screenshotDisplayType`` validation now live in
``app.schemas.screenshots``. They are re-exported here for back-compat with the
existing ``from app.schemas.cpp import Screenshot, is_valid_display_type, ...``
call sites.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Re-exported from the shared screenshot schema module (single source of truth).
from app.schemas.screenshots import (  # noqa: F401
    MAX_SCREENSHOT_BYTES,
    MAX_SCREENSHOT_FILES,
    SCREENSHOT_DISPLAY_TYPES,
    Screenshot,
    ScreenshotSet,
    ScreenshotSetListResponse,
    is_valid_display_type,
)


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
    promotional_text: str | None = None


# ------------------------------------------------------------------
# CPP search keywords (spec 017)
# ------------------------------------------------------------------


class CPPKeywordsResponse(BaseModel):
    """A CPP localization's assigned search keywords, and the ones Apple offers
    for its locale (only an offered ``appKeywords`` id can be assigned)."""

    cpp_id: str
    locale: str
    localization_id: str
    keywords: list[str] = Field(default_factory=list)
    available: list[str] = Field(default_factory=list)


class CPPKeywordRow(BaseModel):
    """``warning`` flags a crowded 4.3(b) category term; it never blocks."""

    keyword: str
    action: Literal["add", "remove", "keep", "error"]
    warning: str | None = None
    error: str | None = None


class CPPKeywordsSetResult(BaseModel):
    """``applied`` stays false while any row is an ``error``. After an apply,
    ``keywords`` is the localization's set read back from Apple."""

    cpp_id: str
    locale: str
    localization_id: str
    applied: bool = False
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list)
    rows: list[CPPKeywordRow] = Field(default_factory=list)
    keywords: list[str] | None = None
