"""Add keyword visibility tracker tables

Revision ID: 005_visibility_tables
Revises: 004_revenuecat_and_clone_ops
Create Date: 2026-05-06

Creates three tables backing the keyword visibility tracker (spec 009):

* ``keyword_visibility_watches`` — per-app (keyword, country) watch list.
* ``keyword_visibility_snapshots`` — one row per poll.
* ``keyword_visibility_results`` — top-N iTunes results inside each snapshot.

Idempotent: the dev workflow calls ``Base.metadata.create_all`` at startup
so the tables may already exist. We only ``create_table`` / ``drop_table``
when needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "005_visibility_tables"
down_revision: Union[str, None] = "004_revenuecat_and_clone_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("keyword_visibility_watches"):
        op.create_table(
            "keyword_visibility_watches",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("text", sa.String(length=255), nullable=False),
            sa.Column("country", sa.String(length=8), nullable=False),
            sa.Column(
                "last_polled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "app_id", "text", "country",
                name="uq_kv_watch_app_text_country",
            ),
        )
        op.create_index(
            "ix_keyword_visibility_watches_app_id",
            "keyword_visibility_watches",
            ["app_id"],
        )
        op.create_index(
            "ix_keyword_visibility_watches_text",
            "keyword_visibility_watches",
            ["text"],
        )

    if not _has_table("keyword_visibility_snapshots"):
        op.create_table(
            "keyword_visibility_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "watch_id",
                sa.Integer(),
                sa.ForeignKey(
                    "keyword_visibility_watches.id", ondelete="CASCADE",
                ),
                nullable=False,
            ),
            sa.Column(
                "polled_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("results_count", sa.Integer(), default=0, nullable=False),
        )
        op.create_index(
            "ix_keyword_visibility_snapshots_watch_id",
            "keyword_visibility_snapshots",
            ["watch_id"],
        )

    if not _has_table("keyword_visibility_results"):
        op.create_table(
            "keyword_visibility_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "snapshot_id",
                sa.Integer(),
                sa.ForeignKey(
                    "keyword_visibility_snapshots.id", ondelete="CASCADE",
                ),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("track_id", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("bundle_id", sa.String(length=255), nullable=False),
            sa.Column("icon_url", sa.String(length=512), nullable=False),
        )
        op.create_index(
            "ix_keyword_visibility_results_snapshot_id",
            "keyword_visibility_results",
            ["snapshot_id"],
        )
        op.create_index(
            "ix_keyword_visibility_results_track_id",
            "keyword_visibility_results",
            ["track_id"],
        )


def downgrade() -> None:
    if _has_table("keyword_visibility_results"):
        op.drop_table("keyword_visibility_results")
    if _has_table("keyword_visibility_snapshots"):
        op.drop_table("keyword_visibility_snapshots")
    if _has_table("keyword_visibility_watches"):
        op.drop_table("keyword_visibility_watches")
