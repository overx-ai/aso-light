"""drop unused subscription_price_points table

Revision ID: 5f914bb9c418
Revises: 11b9535f9089
Create Date: 2026-08-26 01:01:14.065203

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f914bb9c418'
down_revision: Union[str, None] = '11b9535f9089'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_subscription_price_points_subscription_id'), table_name='subscription_price_points')
    op.drop_table('subscription_price_points')


def downgrade() -> None:
    op.create_table('subscription_price_points',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('subscription_id', sa.INTEGER(), nullable=False),
    sa.Column('territory_code', sa.VARCHAR(length=10), nullable=False),
    sa.Column('currency_code', sa.VARCHAR(length=10), nullable=False),
    sa.Column('customer_price', sa.FLOAT(), nullable=False),
    sa.Column('proceeds', sa.FLOAT(), nullable=False),
    sa.Column('price_point_id', sa.VARCHAR(length=255), nullable=False),
    sa.Column('synced_at', sa.DATETIME(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subscription_id', 'price_point_id', name=op.f('uq_sub_price_point'))
    )
    op.create_index(op.f('ix_subscription_price_points_subscription_id'), 'subscription_price_points', ['subscription_id'], unique=False)
