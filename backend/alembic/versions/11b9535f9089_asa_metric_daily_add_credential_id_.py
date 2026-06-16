"""asa_metric_daily add credential_id scoping

Adds a nullable ``credential_id`` FK to ``asa_metric_daily`` so analytics
queries can be scoped to the credential (and thus the user) that synced each
metric row. Without this, two tenants advertising the same Apple app (same
``app_adam_id``) could see each other's metrics.

The column is nullable so the backfill cannot fail on orphan rows; analytics
queries fail closed on NULL (a NULL-credential row is invisible to everyone).
The backfill walks the dim hierarchy
(metric.dim_id -> campaign/ad_group/keyword/search_term -> org -> credential)
via correlated subqueries that run on both SQLite (>= 3.33) and PostgreSQL.

Revision ID: 11b9535f9089
Revises: eb8d54c50e1a
Create Date: 2026-06-16 11:16:01.787283

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11b9535f9089"
down_revision: Union[str, None] = "eb8d54c50e1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "asa_metric_daily"
_COLUMN = "credential_id"
_INDEX = "ix_asa_metric_daily_credential_id"
_FK = "fk_asa_metric_daily_credential"


# Backfill statements: set credential_id by walking from each metric's dim_id
# up through the org to the owning credential. One statement per dim_kind.
_BACKFILL = {
    "CAMPAIGN": """
        UPDATE asa_metric_daily SET credential_id = (
            SELECT o.credential_id FROM asa_campaigns c
            JOIN asa_orgs o ON o.id = c.org_id
            WHERE c.id = asa_metric_daily.dim_id
        ) WHERE dim_kind = 'CAMPAIGN'
    """,
    "AD_GROUP": """
        UPDATE asa_metric_daily SET credential_id = (
            SELECT o.credential_id FROM asa_ad_groups ag
            JOIN asa_campaigns c ON c.id = ag.campaign_id
            JOIN asa_orgs o ON o.id = c.org_id
            WHERE ag.id = asa_metric_daily.dim_id
        ) WHERE dim_kind = 'AD_GROUP'
    """,
    "KEYWORD": """
        UPDATE asa_metric_daily SET credential_id = (
            SELECT o.credential_id FROM asa_keywords k
            JOIN asa_ad_groups ag ON ag.id = k.ad_group_id
            JOIN asa_campaigns c ON c.id = ag.campaign_id
            JOIN asa_orgs o ON o.id = c.org_id
            WHERE k.id = asa_metric_daily.dim_id
        ) WHERE dim_kind = 'KEYWORD'
    """,
    "SEARCH_TERM": """
        UPDATE asa_metric_daily SET credential_id = (
            SELECT o.credential_id FROM asa_search_terms s
            JOIN asa_ad_groups ag ON ag.id = s.ad_group_id
            JOIN asa_campaigns c ON c.id = ag.campaign_id
            JOIN asa_orgs o ON o.id = c.org_id
            WHERE s.id = asa_metric_daily.dim_id
        ) WHERE dim_kind = 'SEARCH_TERM'
    """,
}


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    if is_sqlite:
        # SQLite cannot ADD a FK constraint in place — use batch (table rebuild)
        # for the column + FK, then create the index outside the batch.
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))
            batch.create_foreign_key(
                _FK,
                "asa_credentials",
                [_COLUMN],
                ["id"],
                ondelete="CASCADE",
            )
    else:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))
        op.create_foreign_key(
            _FK,
            _TABLE,
            "asa_credentials",
            [_COLUMN],
            ["id"],
            ondelete="CASCADE",
        )

    op.create_index(_INDEX, _TABLE, [_COLUMN])

    # Backfill credential_id from the dim hierarchy.
    for sql in _BACKFILL.values():
        op.execute(sql)


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    op.drop_index(_INDEX, table_name=_TABLE)
    if is_sqlite:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_FK, type_="foreignkey")
            batch.drop_column(_COLUMN)
    else:
        op.drop_constraint(_FK, _TABLE, type_="foreignkey")
        op.drop_column(_TABLE, _COLUMN)
