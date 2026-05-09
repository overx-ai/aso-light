"""Add metadata localization, state, and translation cache tables

Revision ID: 003_metadata_tables
Revises: 002_territory_gdp
Create Date: 2026-05-05

Creates three new tables backing the Metadata Editor + AI translation feature:

* ``app_metadata_localizations`` — per-locale snapshot of ASC App Info /
  Version localizations.
* ``app_metadata_state`` — one row per app describing the editable version
  + which fields are mutable right now.
* ``metadata_translation_cache`` — caches Claude translations to bound spend.

Idempotent (matches the pattern of 001/002): legacy databases may already have
some of these tables, so we only ``create_table`` / ``drop_table`` when
needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "003_metadata_tables"
down_revision: Union[str, None] = "002_territory_gdp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("app_metadata_localizations"):
        op.create_table(
            "app_metadata_localizations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("asc_localization_id", sa.String(length=255), nullable=False),
            sa.Column("asc_parent_id", sa.String(length=255), nullable=False),
            sa.Column("locale", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=30), nullable=True),
            sa.Column("subtitle", sa.String(length=30), nullable=True),
            sa.Column("privacy_policy_url", sa.String(length=1024), nullable=True),
            sa.Column("description", sa.String(length=4000), nullable=True),
            sa.Column("keywords", sa.String(length=100), nullable=True),
            sa.Column("promotional_text", sa.String(length=170), nullable=True),
            sa.Column("whats_new", sa.String(length=4000), nullable=True),
            sa.Column("marketing_url", sa.String(length=1024), nullable=True),
            sa.Column("support_url", sa.String(length=1024), nullable=True),
            sa.Column(
                "synced_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "app_id", "kind", "locale",
                name="uq_app_metadata_loc_app_kind_locale",
            ),
            sa.CheckConstraint(
                "kind IN ('app_info', 'version')",
                name="ck_app_metadata_loc_kind",
            ),
        )
        op.create_index(
            "ix_app_metadata_localizations_app_id",
            "app_metadata_localizations",
            ["app_id"],
        )
        op.create_index(
            "ix_app_metadata_localizations_kind",
            "app_metadata_localizations",
            ["kind"],
        )
        op.create_index(
            "ix_app_metadata_localizations_locale",
            "app_metadata_localizations",
            ["locale"],
        )

    if not _has_table("app_metadata_state"):
        op.create_table(
            "app_metadata_state",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("editable_version_id", sa.String(length=255), nullable=True),
            sa.Column(
                "editable_version_state", sa.String(length=64), nullable=True,
            ),
            sa.Column("app_info_id", sa.String(length=255), nullable=True),
            sa.Column("editable_fields_json", sa.JSON(), nullable=True),
            sa.Column(
                "last_synced_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if not _has_table("metadata_translation_cache"):
        op.create_table(
            "metadata_translation_cache",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_locale", sa.String(length=16), nullable=False),
            sa.Column("target_locale", sa.String(length=16), nullable=False),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("field_kind", sa.String(length=32), nullable=False),
            sa.Column("translated_text", sa.String(length=4000), nullable=False),
            sa.Column("model", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "app_id",
                "source_locale",
                "target_locale",
                "source_hash",
                "field_kind",
                name="uq_metadata_translation_cache_lookup",
            ),
        )
        op.create_index(
            "ix_metadata_translation_cache_app_id",
            "metadata_translation_cache",
            ["app_id"],
        )
        op.create_index(
            "ix_metadata_translation_cache_app_created",
            "metadata_translation_cache",
            ["app_id", "created_at"],
        )


def downgrade() -> None:
    if _has_table("metadata_translation_cache"):
        op.drop_index(
            "ix_metadata_translation_cache_app_created",
            table_name="metadata_translation_cache",
        )
        op.drop_index(
            "ix_metadata_translation_cache_app_id",
            table_name="metadata_translation_cache",
        )
        op.drop_table("metadata_translation_cache")

    if _has_table("app_metadata_state"):
        op.drop_table("app_metadata_state")

    if _has_table("app_metadata_localizations"):
        op.drop_index(
            "ix_app_metadata_localizations_locale",
            table_name="app_metadata_localizations",
        )
        op.drop_index(
            "ix_app_metadata_localizations_kind",
            table_name="app_metadata_localizations",
        )
        op.drop_index(
            "ix_app_metadata_localizations_app_id",
            table_name="app_metadata_localizations",
        )
        op.drop_table("app_metadata_localizations")
