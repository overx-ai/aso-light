"""listing_drafts

Revision ID: e7c1a3b5d9f2
Revises: d4b2f7a9c1e3
Create Date: 2026-10-28 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.base import UTCDateTime


revision: str = 'e7c1a3b5d9f2'
down_revision: Union[str, None] = 'd4b2f7a9c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'listing_drafts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('proposal_hash', sa.String(length=64), nullable=False),
        sa.Column('values_json', sa.JSON(), nullable=False),
        sa.Column('updated_at', UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['apps.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id'),
    )


def downgrade() -> None:
    op.drop_table('listing_drafts')
