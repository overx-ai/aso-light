"""add keyword_intel_cache

Revision ID: 03e831a0b230
Revises: bb7bbd4f5582
Create Date: 2026-05-09 02:11:18.304296

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '03e831a0b230'
down_revision: Union[str, None] = 'bb7bbd4f5582'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "keyword_intel_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "app_id",
            sa.Integer(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword", sa.String(length=255), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("volume_score", sa.Integer(), nullable=True),
        sa.Column("difficulty_score", sa.Integer(), nullable=True),
        sa.Column("raw_score", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "app_id", "keyword", "locale", "source",
            name="uq_keyword_intel_cache_app_kw_loc_src",
        ),
    )
    op.create_index(
        "ix_keyword_intel_cache_app_id", "keyword_intel_cache", ["app_id"],
    )
    op.create_index(
        "ix_keyword_intel_cache_app_locale_keyword",
        "keyword_intel_cache",
        ["app_id", "locale", "keyword"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_keyword_intel_cache_app_locale_keyword", "keyword_intel_cache",
    )
    op.drop_index("ix_keyword_intel_cache_app_id", "keyword_intel_cache")
    op.drop_table("keyword_intel_cache")
