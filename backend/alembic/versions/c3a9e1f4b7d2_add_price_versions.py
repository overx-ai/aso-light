"""add price versions

Revision ID: c3a9e1f4b7d2
Revises: acc5085799a1
Create Date: 2026-10-03 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.base


revision: str = 'c3a9e1f4b7d2'
down_revision: Union[str, None] = 'acc5085799a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('price_versions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('app_id', sa.Integer(), nullable=False),
    sa.Column('product_kind', sa.String(length=16), nullable=False),
    sa.Column('product_ref_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.String(length=255), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('config', sa.JSON(), nullable=True),
    sa.Column('base_territory_code', sa.String(length=3), nullable=True),
    sa.Column('intro_offer', sa.JSON(), nullable=True),
    sa.Column('items', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['app_id'], ['apps.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('app_id', 'product_kind', 'product_ref_id', 'version', name='uq_price_version_product_version')
    )
    op.create_index(op.f('ix_price_versions_app_id'), 'price_versions', ['app_id'], unique=False)
    op.create_index(op.f('ix_price_versions_user_id'), 'price_versions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_price_versions_user_id'), table_name='price_versions')
    op.drop_index(op.f('ix_price_versions_app_id'), table_name='price_versions')
    op.drop_table('price_versions')
