"""add asa tables

Revision ID: 858cfcb132f1
Revises: 005_visibility_tables
Create Date: 2026-05-08

Creates the nine tables backing the Apple Search Ads vertical (spec 009 ASA):

* ``asa_credentials`` — per-user, Fernet-encrypted ASA API secrets.
* ``asa_orgs`` — advertiser orgs reachable via a credential.
* ``asa_campaigns`` — campaigns; ``app_id`` nullable for orphan ingest.
* ``asa_ad_groups`` — ad groups under a campaign.
* ``asa_keywords`` — bid keywords inside an ad group.
* ``asa_negative_keywords`` — XOR-scoped (campaign or ad-group) via CHECK.
* ``asa_search_terms`` — observed search terms per ad group.
* ``asa_metric_daily`` — polymorphic daily fact table (dim_kind/dim_id).
* ``asa_sync_operations`` — operations log for entity + report syncs.

Idempotent: the dev workflow calls ``Base.metadata.create_all`` at startup
so the tables may already exist. We only ``create_table`` / ``drop_table``
when needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "858cfcb132f1"
down_revision: Union[str, None] = "005_visibility_tables"
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
    # asa_credentials -------------------------------------------------------
    if not _has_table("asa_credentials"):
        op.create_table(
            "asa_credentials",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("client_id_ciphertext", sa.Text(), nullable=False),
            sa.Column("team_id_ciphertext", sa.Text(), nullable=False),
            sa.Column("key_id", sa.String(length=64), nullable=False),
            sa.Column("private_key_ciphertext", sa.Text(), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
        )
        op.create_index(
            "ix_asa_credentials_user_id",
            "asa_credentials",
            ["user_id"],
        )

    # asa_orgs --------------------------------------------------------------
    if not _has_table("asa_orgs"):
        op.create_table(
            "asa_orgs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "credential_id",
                sa.Integer(),
                sa.ForeignKey("asa_credentials.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("asa_org_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=64), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "credential_id",
                "asa_org_id",
                name="uq_asa_org_credential_asaid",
            ),
        )
        op.create_index(
            "ix_asa_orgs_credential_id",
            "asa_orgs",
            ["credential_id"],
        )

    # asa_campaigns ---------------------------------------------------------
    if not _has_table("asa_campaigns"):
        op.create_table(
            "asa_campaigns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "org_id",
                sa.Integer(),
                sa.ForeignKey("asa_orgs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("asa_campaign_id", sa.Integer(), nullable=False),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("app_adam_id", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("supply_sources_json", sa.JSON(), nullable=True),
            sa.Column("daily_budget_amount", sa.Numeric(18, 6), nullable=True),
            sa.Column("daily_budget_currency", sa.String(length=3), nullable=True),
            sa.Column("storefronts_json", sa.JSON(), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "org_id",
                "asa_campaign_id",
                name="uq_asa_campaign_org_asaid",
            ),
        )
        op.create_index("ix_asa_campaigns_org_id", "asa_campaigns", ["org_id"])
        op.create_index("ix_asa_campaigns_app_id", "asa_campaigns", ["app_id"])
        op.create_index(
            "ix_asa_campaigns_app_adam_id",
            "asa_campaigns",
            ["app_adam_id"],
        )

    # asa_ad_groups ---------------------------------------------------------
    if not _has_table("asa_ad_groups"):
        op.create_table(
            "asa_ad_groups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("asa_campaigns.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("asa_ad_group_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("default_bid_amount", sa.Numeric(18, 6), nullable=True),
            sa.Column("default_bid_currency", sa.String(length=3), nullable=True),
            sa.Column("age_range_json", sa.JSON(), nullable=True),
            sa.Column("gender", sa.String(length=16), nullable=True),
            sa.Column("device_class", sa.String(length=32), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "campaign_id",
                "asa_ad_group_id",
                name="uq_asa_ad_group_campaign_asaid",
            ),
        )
        op.create_index(
            "ix_asa_ad_groups_campaign_id",
            "asa_ad_groups",
            ["campaign_id"],
        )

    # asa_keywords ----------------------------------------------------------
    if not _has_table("asa_keywords"):
        op.create_table(
            "asa_keywords",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "ad_group_id",
                sa.Integer(),
                sa.ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("asa_keyword_id", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(length=255), nullable=False),
            sa.Column("match_type", sa.String(length=16), nullable=False),
            sa.Column("bid_amount", sa.Numeric(18, 6), nullable=True),
            sa.Column("bid_currency", sa.String(length=3), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "ad_group_id",
                "asa_keyword_id",
                name="uq_asa_keyword_adgroup_asaid",
            ),
        )
        op.create_index(
            "ix_asa_keywords_ad_group_id",
            "asa_keywords",
            ["ad_group_id"],
        )
        op.create_index("ix_asa_keywords_text", "asa_keywords", ["text"])

    # asa_negative_keywords -------------------------------------------------
    if not _has_table("asa_negative_keywords"):
        op.create_table(
            "asa_negative_keywords",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("asa_campaigns.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "ad_group_id",
                sa.Integer(),
                sa.ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("asa_negative_keyword_id", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(length=255), nullable=False),
            sa.Column("match_type", sa.String(length=16), nullable=False),
            sa.Column("scope", sa.String(length=16), nullable=False),
            *_ts_columns(),
            sa.CheckConstraint(
                "(campaign_id IS NULL) <> (ad_group_id IS NULL)",
                name="ck_asa_negative_exactly_one_scope",
            ),
        )
        op.create_index(
            "ix_asa_negative_keywords_campaign_id",
            "asa_negative_keywords",
            ["campaign_id"],
        )
        op.create_index(
            "ix_asa_negative_keywords_ad_group_id",
            "asa_negative_keywords",
            ["ad_group_id"],
        )

    # asa_search_terms ------------------------------------------------------
    if not _has_table("asa_search_terms"):
        op.create_table(
            "asa_search_terms",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "ad_group_id",
                sa.Integer(),
                sa.ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("text", sa.String(length=255), nullable=False),
            sa.Column("match_type", sa.String(length=16), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "ad_group_id",
                "text",
                "match_type",
                name="uq_asa_search_term_adgroup_text_match",
            ),
        )
        op.create_index(
            "ix_asa_search_terms_ad_group_id",
            "asa_search_terms",
            ["ad_group_id"],
        )
        op.create_index(
            "ix_asa_search_terms_text",
            "asa_search_terms",
            ["text"],
        )

    # asa_metric_daily ------------------------------------------------------
    if not _has_table("asa_metric_daily"):
        op.create_table(
            "asa_metric_daily",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("dim_kind", sa.String(length=16), nullable=False),
            sa.Column("dim_id", sa.Integer(), nullable=False),
            sa.Column("app_adam_id", sa.String(length=32), nullable=False),
            sa.Column("date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("storefront", sa.String(length=8), nullable=True),
            sa.Column("impressions", sa.Integer(), default=0, nullable=False),
            sa.Column("taps", sa.Integer(), default=0, nullable=False),
            sa.Column("installs", sa.Integer(), default=0, nullable=False),
            sa.Column("new_downloads", sa.Integer(), default=0, nullable=False),
            sa.Column("redownloads", sa.Integer(), default=0, nullable=False),
            sa.Column("spend_amount", sa.Numeric(18, 6), default=0, nullable=False),
            sa.Column("spend_currency", sa.String(length=3), nullable=False),
            sa.Column("avg_cpa_amount", sa.Numeric(18, 6), nullable=True),
            sa.Column("avg_cpt_amount", sa.Numeric(18, 6), nullable=True),
            sa.Column("ttr", sa.Numeric(8, 6), nullable=True),
            sa.Column("conversion_rate", sa.Numeric(8, 6), nullable=True),
            *_ts_columns(),
            sa.UniqueConstraint(
                "dim_kind",
                "dim_id",
                "date",
                "storefront",
                name="uq_asa_metric_daily_grain",
            ),
        )
        op.create_index(
            "ix_asa_metric_daily_dim_id",
            "asa_metric_daily",
            ["dim_id"],
        )
        op.create_index(
            "ix_asa_metric_daily_app_adam_id",
            "asa_metric_daily",
            ["app_adam_id"],
        )
        op.create_index(
            "ix_asa_metric_daily_app_date",
            "asa_metric_daily",
            ["app_adam_id", "date"],
        )

    # asa_sync_operations ---------------------------------------------------
    if not _has_table("asa_sync_operations"):
        op.create_table(
            "asa_sync_operations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "credential_id",
                sa.Integer(),
                sa.ForeignKey("asa_credentials.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("full_backfill", sa.Boolean(), default=False, nullable=False),
            sa.Column("steps_json", sa.JSON(), nullable=True),
            sa.Column("error_log_json", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_columns(),
        )
        op.create_index(
            "ix_asa_sync_operations_credential_id",
            "asa_sync_operations",
            ["credential_id"],
        )


def downgrade() -> None:
    # Drop in reverse FK dependency order.
    for table in (
        "asa_sync_operations",
        "asa_metric_daily",
        "asa_search_terms",
        "asa_negative_keywords",
        "asa_keywords",
        "asa_ad_groups",
        "asa_campaigns",
        "asa_orgs",
        "asa_credentials",
    ):
        if _has_table(table):
            op.drop_table(table)
