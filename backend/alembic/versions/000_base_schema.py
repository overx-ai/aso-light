"""create baseline schema for migration-first bootstrapping

Revision ID: 000_base_schema
Revises:
Create Date: 2026-05-09

This revision snapshots the original core schema that historically came from
application-startup ORM table creation. New empty databases can now bootstrap
through Alembic alone, while later revisions continue to apply their additive
changes on top.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000_base_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


metadata = sa.MetaData()

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("email", sa.String(length=320), nullable=False, unique=True, index=True),
    sa.Column("password_hash", sa.String(length=128), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
)

territories = sa.Table(
    "territories",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("code", sa.String(length=3), nullable=False, unique=True, index=True),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("currency_code", sa.String(length=3), nullable=False),
    sa.Column("vat_rate", sa.Float(), nullable=False, server_default=sa.text("0")),
    sa.Column("apple_territory_id", sa.String(length=255), nullable=True),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
)

asc_credentials = sa.Table(
    "asc_credentials",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("issuer_id", sa.String(length=255), nullable=False),
    sa.Column("key_id", sa.String(length=255), nullable=False),
    sa.Column("private_key_encrypted", sa.Text(), nullable=False),
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
)

apps = sa.Table(
    "apps",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "credential_id",
        sa.Integer(),
        sa.ForeignKey("asc_credentials.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("asc_app_id", sa.String(length=255), nullable=False),
    sa.Column("bundle_id", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("platform", sa.String(length=10), nullable=False),
    sa.Column("icon_url", sa.String(length=1024), nullable=True),
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
)

price_presets = sa.Table(
    "price_presets",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column(
        "base_territory_code",
        sa.String(length=3),
        nullable=False,
        server_default=sa.text("'US'"),
    ),
    sa.Column("base_price", sa.Float(), nullable=False),
    sa.Column("index_type", sa.String(length=20), nullable=False),
    sa.Column("apply_vat", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column(
        "charming_mode",
        sa.String(length=10),
        nullable=False,
        server_default=sa.text("'none'"),
    ),
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
)

economic_indices = sa.Table(
    "economic_indices",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "territory_id",
        sa.Integer(),
        sa.ForeignKey("territories.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("index_type", sa.String(length=20), nullable=False),
    sa.Column("value", sa.Float(), nullable=False),
    sa.Column("reference_date", sa.Date(), nullable=False),
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
    sa.UniqueConstraint(
        "territory_id",
        "index_type",
        name="uq_economic_index_territory_type",
    ),
)

keywords = sa.Table(
    "keywords",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("text", sa.String(length=255), nullable=False, index=True),
    sa.Column("locale", sa.String(length=10), nullable=False),
    sa.Column("popularity", sa.Integer(), nullable=True),
    sa.Column("popularity_updated_at", sa.DateTime(timezone=True), nullable=True),
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
    sa.UniqueConstraint("text", "locale", name="uq_keyword_text_locale"),
)

keyword_trackings = sa.Table(
    "keyword_trackings",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False, index=True),
    sa.Column(
        "keyword_id",
        sa.Integer(),
        sa.ForeignKey("keywords.id"),
        nullable=False,
        index=True,
    ),
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
    sa.UniqueConstraint("app_id", "keyword_id", name="uq_keyword_tracking_app_keyword"),
)

keyword_rankings = sa.Table(
    "keyword_rankings",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "tracking_id",
        sa.Integer(),
        sa.ForeignKey("keyword_trackings.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("territory_id", sa.Integer(), sa.ForeignKey("territories.id"), nullable=False),
    sa.Column("rank", sa.Integer(), nullable=True),
    sa.Column(
        "recorded_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    ),
)

keyword_locale_indices = sa.Table(
    "keyword_locale_indices",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("locale", sa.String(length=10), nullable=False),
    sa.Column("territory_code", sa.String(length=3), nullable=False),
    sa.Column("is_indexed", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("notes", sa.String(length=500), nullable=True),
    sa.UniqueConstraint(
        "locale",
        "territory_code",
        name="uq_keyword_locale_index_locale_territory",
    ),
)

competitor_apps = sa.Table(
    "competitor_apps",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False, index=True),
    sa.Column("asc_app_id", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("bundle_id", sa.String(length=255), nullable=True),
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
)

subscription_groups = sa.Table(
    "subscription_groups",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False, index=True),
    sa.Column("asc_group_id", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
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
)

subscriptions = sa.Table(
    "subscriptions",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "group_id",
        sa.Integer(),
        sa.ForeignKey("subscription_groups.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("asc_subscription_id", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("product_id", sa.String(length=255), nullable=False),
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
)

subscription_prices = sa.Table(
    "subscription_prices",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "subscription_id",
        sa.Integer(),
        sa.ForeignKey("subscriptions.id"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "territory_id",
        sa.Integer(),
        sa.ForeignKey("territories.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("price_point_id", sa.String(length=255), nullable=True),
    sa.Column("customer_price", sa.Float(), nullable=False),
    sa.Column("proceeds", sa.Float(), nullable=False),
    sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
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
    sa.UniqueConstraint(
        "subscription_id",
        "territory_id",
        name="uq_subscription_price_sub_territory",
    ),
)

subscription_price_points = sa.Table(
    "subscription_price_points",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "subscription_id",
        sa.Integer(),
        sa.ForeignKey("subscriptions.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("territory_code", sa.String(length=10), nullable=False),
    sa.Column("currency_code", sa.String(length=10), nullable=False),
    sa.Column("customer_price", sa.Float(), nullable=False),
    sa.Column("proceeds", sa.Float(), nullable=False),
    sa.Column("price_point_id", sa.String(length=255), nullable=False),
    sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
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
    sa.UniqueConstraint(
        "subscription_id",
        "price_point_id",
        name="uq_sub_price_point",
    ),
)

in_app_purchases = sa.Table(
    "in_app_purchases",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False, index=True),
    sa.Column("asc_iap_id", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("product_id", sa.String(length=255), nullable=False),
    sa.Column("iap_type", sa.String(length=20), nullable=False),
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
)

iap_prices = sa.Table(
    "iap_prices",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("iap_id", sa.Integer(), sa.ForeignKey("in_app_purchases.id"), nullable=False, index=True),
    sa.Column(
        "territory_id",
        sa.Integer(),
        sa.ForeignKey("territories.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("price_point_id", sa.String(length=255), nullable=True),
    sa.Column("customer_price", sa.Float(), nullable=False),
    sa.Column("proceeds", sa.Float(), nullable=False),
    sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
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
    sa.UniqueConstraint("iap_id", "territory_id", name="uq_iap_price_iap_territory"),
)

def upgrade() -> None:
    metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata.drop_all(op.get_bind(), checkfirst=True)
