"""Schemas for metadata editor + cross-localization + AI translation.

Mirrors the request/response shapes consumed by ``app/api/v1/metadata.py``
and produced by ``app/services/metadata/{bulk,snapshot,translate}.py``.

All field-level validation delegates to
``app.services.metadata.validation`` so the bulk service and the per-locale
upsert path enforce the same Apple char limits and URL rules.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.metadata.validation import ALL_FIELDS, validate_field

MetadataKind = Literal["app_info", "version"]
KeywordPlacement = Literal["title", "subtitle", "keywords", "none"]
BulkApplyStatus = Literal["applied", "skipped", "failed"]

# Mirrors the ASC 50-locale-per-bulk-request guard documented in the spec.
MAX_BULK_TARGET_LOCALES: int = 50


# ------------------------------------------------------------------
# Snapshot read shapes
# ------------------------------------------------------------------


class AppMetadataLocalizationOut(BaseModel):
    """Single per-locale metadata row from the snapshot cache."""

    id: int
    app_id: int
    kind: MetadataKind
    asc_localization_id: str
    asc_parent_id: str
    locale: str

    name: str | None = None
    subtitle: str | None = None
    privacy_policy_url: str | None = None

    description: str | None = None
    keywords: str | None = None
    promotional_text: str | None = None
    whats_new: str | None = None
    marketing_url: str | None = None
    support_url: str | None = None

    synced_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AppMetadataStateOut(BaseModel):
    """Per-app editable-version flags driving UI greying."""

    editable_version_id: str | None = None
    editable_version_state: str | None = None
    app_info_id: str | None = None
    editable_fields: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, model) -> "AppMetadataStateOut":
        """Build from ``AppMetadataState`` ORM row.

        The model stores ``editable_fields_json`` (free-form JSON list);
        we expose it as ``editable_fields`` defaulting to ``[]`` so the
        UI never has to null-check.
        """
        return cls(
            editable_version_id=model.editable_version_id,
            editable_version_state=model.editable_version_state,
            app_info_id=model.app_info_id,
            editable_fields=list(model.editable_fields_json or []),
            last_synced_at=model.last_synced_at,
        )


class AppMetadataSnapshotOut(BaseModel):
    """Combined snapshot returned by ``GET /apps/{app_id}/metadata``."""

    app_info: list[AppMetadataLocalizationOut] = Field(default_factory=list)
    versions: list[AppMetadataLocalizationOut] = Field(default_factory=list)
    state: AppMetadataStateOut


# ------------------------------------------------------------------
# Single-locale upsert
# ------------------------------------------------------------------


class LocaleUpsertIn(BaseModel):
    """Create / partial-update body for a single locale.

    All fields optional; only the ones provided (non-None) are touched.
    Each provided field is validated against
    :func:`app.services.metadata.validation.validate_field`.
    """

    name: str | None = None
    subtitle: str | None = None
    privacy_policy_url: str | None = None
    description: str | None = None
    keywords: str | None = None
    promotional_text: str | None = None
    whats_new: str | None = None
    marketing_url: str | None = None
    support_url: str | None = None

    @field_validator(
        "name",
        "subtitle",
        "privacy_policy_url",
        "description",
        "keywords",
        "promotional_text",
        "whats_new",
        "marketing_url",
        "support_url",
    )
    @classmethod
    def _check_field(cls, value, info):
        ok, err = validate_field(info.field_name, value)
        if not ok:
            raise ValueError(err)
        return value


# ------------------------------------------------------------------
# Bulk fan-out
# ------------------------------------------------------------------


class BulkPreviewIn(BaseModel):
    """Bulk preview request: a single field/value fanned out to N locales.

    Two modes:

    * **Same value** — set ``value`` and leave ``values_by_locale`` null; the
      same string is fanned out to every target locale.
    * **Localized values** — set ``values_by_locale`` (locale → string) for
      translated metadata; every target locale must have an entry. ``value``
      is ignored in this mode.
    """

    field: str
    value: str | None = None
    target_locales: list[str] = Field(default_factory=list)
    values_by_locale: dict[str, str | None] | None = None

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        if v not in ALL_FIELDS:
            raise ValueError(
                f"Unknown metadata field: {v!r}. "
                f"Must be one of {sorted(ALL_FIELDS)}"
            )
        return v

    @field_validator("target_locales")
    @classmethod
    def _cap_locales(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_BULK_TARGET_LOCALES:
            raise ValueError(
                f"target_locales capped at {MAX_BULK_TARGET_LOCALES}, "
                f"got {len(v)}"
            )
        return v


class BulkPreviewItem(BaseModel):
    """Per-locale row of the bulk preview diff."""

    locale: str
    current_value: str | None = None
    new_value: str | None = None
    char_overflow_by: int = 0
    would_skip: bool = False
    reason: str | None = None


class BulkPreviewOut(BaseModel):
    """Diff list returned by ``POST .../metadata/bulk/preview``."""

    items: list[BulkPreviewItem] = Field(default_factory=list)


class BulkApplyIn(BulkPreviewIn):
    """Same payload as preview, plus a ``force`` override flag."""

    force: bool = False


class BulkApplyResult(BaseModel):
    """Per-locale result row of a bulk apply."""

    locale: str
    status: BulkApplyStatus
    error: str | None = None


class BulkApplyOut(BaseModel):
    """Aggregate result of ``POST .../metadata/bulk/apply``."""

    applied: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[BulkApplyResult] = Field(default_factory=list)


# ------------------------------------------------------------------
# Translation
# ------------------------------------------------------------------


class TranslateIn(BaseModel):
    """Request body for AI translation suggestions (never auto-applies)."""

    source_locale: str
    target_locales: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)

    @field_validator("target_locales")
    @classmethod
    def _cap_locales(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_BULK_TARGET_LOCALES:
            raise ValueError(
                f"target_locales capped at {MAX_BULK_TARGET_LOCALES}, "
                f"got {len(v)}"
            )
        return v

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, v: list[str]) -> list[str]:
        unknown = [f for f in v if f not in ALL_FIELDS]
        if unknown:
            raise ValueError(
                f"Unknown metadata fields: {unknown}. "
                f"Must be a subset of {sorted(ALL_FIELDS)}"
            )
        return v


class TranslateSuggestionItem(BaseModel):
    """A single Claude-generated suggestion for a (locale, field) pair."""

    locale: str
    field: str
    suggestion: str
    cached: bool = False


class TranslateOut(BaseModel):
    """Response shape for ``POST .../metadata/translate``."""

    items: list[TranslateSuggestionItem] = Field(default_factory=list)


# ------------------------------------------------------------------
# Keyword coverage
# ------------------------------------------------------------------


class KeywordCoverageItem(BaseModel):
    """Where a tracked keyword surfaces inside a locale's metadata."""

    keyword: str
    locale: str
    placement: KeywordPlacement


class KeywordCoverageOut(BaseModel):
    """Response shape for ``GET .../metadata/keyword-coverage``."""

    items: list[KeywordCoverageItem] = Field(default_factory=list)


# ------------------------------------------------------------------
# Cross-localization grid
# ------------------------------------------------------------------


class CrossLocalizationGridItem(BaseModel):
    """One territory row of the cross-localization grid.

    ``has_metadata`` is populated only when the request carries an
    ``app_id`` context (so the same endpoint can serve the
    territories-only catalog view); otherwise it defaults to ``False``.
    """

    territory_code: str
    locale: str
    gdp_per_capita_usd: float | None = None
    has_metadata: bool = False


class CrossLocalizationGridOut(BaseModel):
    """Response shape for ``GET /keywords/cross-localization-grid``."""

    items: list[CrossLocalizationGridItem] = Field(default_factory=list)
