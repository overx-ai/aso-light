"""keyword_intel_cache.popularity_source

Revision ID: d4b2f7a9c1e3
Revises: c3a9e1f4b7d2
Create Date: 2026-10-28 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4b2f7a9c1e3'
down_revision: Union[str, None] = 'c3a9e1f4b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('keyword_intel_cache') as batch_op:
        batch_op.add_column(sa.Column('popularity_source', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('keyword_intel_cache') as batch_op:
        batch_op.drop_column('popularity_source')
