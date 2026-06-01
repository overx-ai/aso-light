"""add review_theme_cache

Revision ID: bb7bbd4f5582
Revises: 858cfcb132f1
Create Date: 2026-05-09 01:49:56.863253

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb7bbd4f5582'
down_revision: Union[str, None] = '858cfcb132f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_theme_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "app_id",
            sa.Integer(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("theme", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "app_id", "review_id", name="uq_review_theme_cache_app_review",
        ),
    )
    op.create_index(
        "ix_review_theme_cache_app_id", "review_theme_cache", ["app_id"],
    )
    op.create_index(
        "ix_review_theme_cache_app_theme",
        "review_theme_cache",
        ["app_id", "theme"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_theme_cache_app_theme", "review_theme_cache")
    op.drop_index("ix_review_theme_cache_app_id", "review_theme_cache")
    op.drop_table("review_theme_cache")
