"""Metadata Editor + AI translation models.

Three tables back the in-app metadata editor:

* :class:`AppMetadataLocalization` — snapshot of per-locale ASC metadata
  (App Info + Version localizations) so the UI can read without hammering ASC.
* :class:`AppMetadataState` — one row per app describing which version is
  currently editable and which fields ASC will accept right now.
* :class:`MetadataTranslationCache` — bounds Anthropic spend; lookup key is
  ``(app_id, source_locale, target_locale, source_hash, field_kind)``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AppMetadataLocalization(Base):
    """Cached per-locale metadata pulled from ASC.

    ``kind`` is a free-form string column (with a CheckConstraint) rather than
    a Python ``Enum`` to keep the migration trivial across SQLite + Postgres.
    """

    __tablename__ = "app_metadata_localizations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    asc_localization_id: Mapped[str] = mapped_column(String(255))
    asc_parent_id: Mapped[str] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(16), index=True)

    # app_info fields
    name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subtitle: Mapped[str | None] = mapped_column(String(30), nullable=True)
    privacy_policy_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True,
    )

    # version fields
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    keywords: Mapped[str | None] = mapped_column(String(100), nullable=True)
    promotional_text: Mapped[str | None] = mapped_column(
        String(170), nullable=True,
    )
    whats_new: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    marketing_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True,
    )
    support_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "app_id", "kind", "locale",
            name="uq_app_metadata_loc_app_kind_locale",
        ),
        CheckConstraint(
            "kind IN ('app_info', 'version')",
            name="ck_app_metadata_loc_kind",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AppMetadataLocalization app_id={self.app_id} "
            f"kind={self.kind!r} locale={self.locale!r}>"
        )


class AppMetadataState(Base):
    """Per-app snapshot of which ASC version is editable + which fields."""

    __tablename__ = "app_metadata_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), unique=True,
    )
    editable_version_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    editable_version_state: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    app_info_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    editable_fields_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AppMetadataState app_id={self.app_id} "
            f"editable_version_id={self.editable_version_id!r}>"
        )


class MetadataTranslationCache(Base):
    """Cache of Claude translations keyed by source hash + field kind.

    Cuts duplicate API calls (and the resulting Anthropic spend) when the
    same source string is re-translated to the same locale.
    """

    __tablename__ = "metadata_translation_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True,
    )
    source_locale: Mapped[str] = mapped_column(String(16))
    target_locale: Mapped[str] = mapped_column(String(16))
    source_hash: Mapped[str] = mapped_column(String(64))
    field_kind: Mapped[str] = mapped_column(String(32))
    translated_text: Mapped[str] = mapped_column(String(4000))
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "source_locale",
            "target_locale",
            "source_hash",
            "field_kind",
            name="uq_metadata_translation_cache_lookup",
        ),
        Index(
            "ix_metadata_translation_cache_app_created",
            "app_id",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MetadataTranslationCache app_id={self.app_id} "
            f"{self.source_locale}->{self.target_locale} "
            f"field={self.field_kind!r}>"
        )
