"""add personal access tokens table

Revision ID: 006_personal_access_tokens
Revises: 858cfcb132f1
Create Date: 2026-05-09

Adds the table backing long-lived personal access tokens used by `/mcp` and
the `/api/v1/auth/tokens` REST endpoints.

Idempotent: the dev workflow calls ``Base.metadata.create_all`` at startup
so the table may already exist. We only create/drop it when needed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "006_personal_access_tokens"
down_revision: Union[str, None] = "858cfcb132f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("personal_access_tokens"):
        op.create_table(
            "personal_access_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        )
        op.create_index(
            "ix_personal_access_tokens_user_id",
            "personal_access_tokens",
            ["user_id"],
        )
        op.create_index(
            "ix_personal_access_tokens_token_hash",
            "personal_access_tokens",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    if _has_table("personal_access_tokens"):
        op.drop_table("personal_access_tokens")
