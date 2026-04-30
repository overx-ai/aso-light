"""add config json column to price_presets

Revision ID: 001_preset_config
Revises:
Create Date: 2026-04-26

This is the first migration. The dev workflow currently bootstraps the schema
via ``Base.metadata.create_all`` on app startup, so the ``price_presets.config``
column may already exist by the time anyone runs ``alembic upgrade head``.
The migration is therefore written idempotently — it inspects the live schema
and only adds (or drops) the column when needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_preset_config"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("price_presets", "config"):
        op.add_column(
            "price_presets",
            sa.Column("config", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("price_presets", "config"):
        op.drop_column("price_presets", "config")
