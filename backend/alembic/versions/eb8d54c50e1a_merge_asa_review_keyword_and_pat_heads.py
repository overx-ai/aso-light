"""merge asa/review/keyword and pat heads

Revision ID: eb8d54c50e1a
Revises: 03e831a0b230, 8598f1d5d1c2
Create Date: 2026-06-11 21:37:39.343907

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb8d54c50e1a'
down_revision: Union[str, None] = ('03e831a0b230', '8598f1d5d1c2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
