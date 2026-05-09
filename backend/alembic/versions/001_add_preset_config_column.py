"""add config json column to price_presets

Revision ID: 001_preset_config
Revises:
Create Date: 2026-04-26

This is the first migration. It remains idempotent so databases that were
bootstrapped before the migration-first startup switch can still converge on
the Alembic history safely.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_preset_config"
down_revision: Union[str, None] = "000_base_schema"
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
