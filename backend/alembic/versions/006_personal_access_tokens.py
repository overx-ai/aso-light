"""Add personal_access_tokens table

Revision ID: 006_personal_access_tokens
Revises: 858cfcb132f1
Create Date: 2026-05-09

Creates the table backing API-issued personal access tokens used by
``/api/v1/auth/tokens`` and PAT-authenticated ``/mcp`` requests.

Idempotent: the dev workflow calls ``Base.metadata.create_all`` at startup
so the table may already exist. We only create or drop the table when needed.
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


def _ts_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    if _has_table("personal_access_tokens"):
        return

    op.create_table(
        "personal_access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_personal_access_tokens_token_hash",
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
    )


def downgrade() -> None:
    if not _has_table("personal_access_tokens"):
        return

    op.drop_index(
        "ix_personal_access_tokens_token_hash",
        table_name="personal_access_tokens",
    )
    op.drop_index(
        "ix_personal_access_tokens_user_id",
        table_name="personal_access_tokens",
    )
    op.drop_table("personal_access_tokens")
