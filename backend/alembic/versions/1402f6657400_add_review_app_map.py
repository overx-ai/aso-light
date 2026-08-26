"""add review app map

Revision ID: 1402f6657400
Revises: 5f914bb9c418
Create Date: 2026-08-26 10:45:10.628789

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1402f6657400'
down_revision: Union[str, None] = '5f914bb9c418'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_app_map",
        sa.Column("review_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "app_id",
            sa.Integer(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_review_app_map_app_id", "review_app_map", ["app_id"])

    op.create_table(
        "review_response_map",
        sa.Column("response_id", sa.String(length=64), primary_key=True),
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_review_response_map_review_id", "review_response_map", ["review_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_response_map_review_id", "review_response_map")
    op.drop_table("review_response_map")
    op.drop_index("ix_review_app_map_app_id", "review_app_map")
    op.drop_table("review_app_map")
