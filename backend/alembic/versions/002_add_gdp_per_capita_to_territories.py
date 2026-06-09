"""add gdp_per_capita_usd column to territories

Revision ID: 002_territory_gdp
Revises: 001_preset_config
Create Date: 2026-05-05

Adds a nullable ``gdp_per_capita_usd`` (Float) column to ``territories``.
Powers default GDP-sort on the cross-localization metadata grid.

Idempotent in line with 001_preset_config so pre-migration-first databases can
still converge safely; we only ``add_column`` / ``drop_column`` when the
column actually exists / is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "002_territory_gdp"
down_revision: Union[str, None] = "001_preset_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("territories", "gdp_per_capita_usd"):
        op.add_column(
            "territories",
            sa.Column("gdp_per_capita_usd", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("territories", "gdp_per_capita_usd"):
        op.drop_column("territories", "gdp_per_capita_usd")
