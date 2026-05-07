from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class ASACredential(TimestampMixin, Base):
    """Per-user Apple Search Ads API credential.

    All secrets (clientId, teamId, .p8 private key) are Fernet-encrypted at
    rest via `app.core.security.{encrypt_value, decrypt_value}`.
    """

    __tablename__ = "asa_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120))
    client_id_ciphertext: Mapped[str] = mapped_column(Text)
    team_id_ciphertext: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(64))
    private_key_ciphertext: Mapped[str] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ASAOrg(TimestampMixin, Base):
    """An ASA org (advertiser) reachable via a credential."""

    __tablename__ = "asa_orgs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"),
        index=True,
    )
    asa_org_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            "asa_org_id",
            name="uq_asa_org_credential_asaid",
        ),
    )


class ASACampaign(TimestampMixin, Base):
    """An ASA campaign. `app_id` is nullable to allow ingest of campaigns
    targeting apps that the user has not (yet) added to ASO-Light."""

    __tablename__ = "asa_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("asa_orgs.id", ondelete="CASCADE"),
        index=True,
    )
    asa_campaign_id: Mapped[int] = mapped_column(Integer)
    app_id: Mapped[int | None] = mapped_column(
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    app_adam_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    supply_sources_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    daily_budget_amount: Mapped[float | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    daily_budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    storefronts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "asa_campaign_id",
            name="uq_asa_campaign_org_asaid",
        ),
    )


class ASAAdGroup(TimestampMixin, Base):
    __tablename__ = "asa_ad_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("asa_campaigns.id", ondelete="CASCADE"),
        index=True,
    )
    asa_ad_group_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    default_bid_amount: Mapped[float | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    default_bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    age_range_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "asa_ad_group_id",
            name="uq_asa_ad_group_campaign_asaid",
        ),
    )


class ASAKeyword(TimestampMixin, Base):
    __tablename__ = "asa_keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ad_group_id: Mapped[int] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
        index=True,
    )
    asa_keyword_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16))  # BROAD | EXACT
    bid_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "ad_group_id",
            "asa_keyword_id",
            name="uq_asa_keyword_adgroup_asaid",
        ),
    )


class ASANegativeKeyword(TimestampMixin, Base):
    """Negative keyword scoped to either a campaign or an ad group (XOR)."""

    __tablename__ = "asa_negative_keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("asa_campaigns.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    ad_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    asa_negative_keyword_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(255))
    match_type: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))  # CAMPAIGN | AD_GROUP

    __table_args__ = (
        CheckConstraint(
            "(campaign_id IS NULL) <> (ad_group_id IS NULL)",
            name="ck_asa_negative_exactly_one_scope",
        ),
    )


class ASASearchTerm(TimestampMixin, Base):
    """Observed search term per ad group. Composite uniqueness on
    (ad_group_id, text, match_type) so a phrase reported under different
    match types does not collide."""

    __tablename__ = "asa_search_terms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ad_group_id: Mapped[int] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
        index=True,
    )
    text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))  # SEARCHTERM | RAW
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "ad_group_id",
            "text",
            "match_type",
            name="uq_asa_search_term_adgroup_text_match",
        ),
    )


class ASAMetricDaily(TimestampMixin, Base):
    """Polymorphic daily metric fact table.

    `dim_kind` selects which dimension the row pertains to (CAMPAIGN, AD_GROUP,
    KEYWORD, SEARCH_TERM); `dim_id` is the FK-like reference into the
    corresponding table. `app_adam_id` is denormalized so we can roll up by app
    without joining the dim hierarchy.
    """

    __tablename__ = "asa_metric_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dim_kind: Mapped[str] = mapped_column(String(16))  # CAMPAIGN|AD_GROUP|KEYWORD|SEARCH_TERM
    dim_id: Mapped[int] = mapped_column(Integer, index=True)
    app_adam_id: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    storefront: Mapped[str | None] = mapped_column(String(8), nullable=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    taps: Mapped[int] = mapped_column(Integer, default=0)
    installs: Mapped[int] = mapped_column(Integer, default=0)
    new_downloads: Mapped[int] = mapped_column(Integer, default=0)
    redownloads: Mapped[int] = mapped_column(Integer, default=0)
    spend_amount: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    spend_currency: Mapped[str] = mapped_column(String(3))
    avg_cpa_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    avg_cpt_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    ttr: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    conversion_rate: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "dim_kind",
            "dim_id",
            "date",
            "storefront",
            name="uq_asa_metric_daily_grain",
        ),
        Index("ix_asa_metric_daily_app_date", "app_adam_id", "date"),
    )


class ASASyncOperation(TimestampMixin, Base):
    """Operations log for ASA pull syncs (entities + reports)."""

    __tablename__ = "asa_sync_operations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(16))
    full_backfill: Mapped[bool] = mapped_column(default=False)
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_log_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
