"""SQLAlchemy 2.0 models for the Apple Search Ads vertical.

Hierarchy: ASACredential (per user) -> ASAOrg (per credential) ->
ASACampaign (per org, optionally bound to a local App by adam_id) ->
ASAAdGroup -> ASAKeyword | ASANegativeKeyword | ASASearchTerm. The fact
table ASAMetricDaily is polymorphic: each row references one dimension
via the (dim_kind, dim_id) pair and denormalizes app_adam_id for fast
app-scoped rollups.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.app import App


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

    orgs: Mapped[list[ASAOrg]] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ASAOrg(TimestampMixin, Base):
    """An ASA org (advertiser) reachable via a credential."""

    __tablename__ = "asa_orgs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"),
        index=True,
    )
    asa_org_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    credential: Mapped[ASACredential] = relationship(back_populates="orgs")
    campaigns: Mapped[list[ASACampaign]] = relationship(
        back_populates="org",
        cascade="all, delete-orphan",
    )

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
    asa_campaign_id: Mapped[int]
    app_id: Mapped[int | None] = mapped_column(
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    app_adam_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    supply_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    daily_budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    daily_budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    storefronts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    org: Mapped[ASAOrg] = relationship(back_populates="campaigns")
    ad_groups: Mapped[list[ASAAdGroup]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    negative_keywords: Mapped[list[ASANegativeKeyword]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        foreign_keys="ASANegativeKeyword.campaign_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "asa_campaign_id",
            name="uq_asa_campaign_org_asaid",
        ),
    )


class ASAAdGroup(TimestampMixin, Base):
    """An ASA ad group inside a campaign."""

    __tablename__ = "asa_ad_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("asa_campaigns.id", ondelete="CASCADE"),
        index=True,
    )
    asa_ad_group_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    default_bid_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    default_bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    age_range: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    campaign: Mapped[ASACampaign] = relationship(back_populates="ad_groups")
    keywords: Mapped[list[ASAKeyword]] = relationship(
        back_populates="ad_group",
        cascade="all, delete-orphan",
    )
    negative_keywords: Mapped[list[ASANegativeKeyword]] = relationship(
        back_populates="ad_group",
        cascade="all, delete-orphan",
        foreign_keys="ASANegativeKeyword.ad_group_id",
    )
    search_terms: Mapped[list[ASASearchTerm]] = relationship(
        back_populates="ad_group",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "asa_ad_group_id",
            name="uq_asa_ad_group_campaign_asaid",
        ),
    )


class ASAKeyword(TimestampMixin, Base):
    """A targeted keyword inside an ad group."""

    __tablename__ = "asa_keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ad_group_id: Mapped[int] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"),
        index=True,
    )
    asa_keyword_id: Mapped[int]
    text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16))  # BROAD | EXACT
    bid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ad_group: Mapped[ASAAdGroup] = relationship(back_populates="keywords")

    __table_args__ = (
        UniqueConstraint(
            "ad_group_id",
            "asa_keyword_id",
            name="uq_asa_keyword_adgroup_asaid",
        ),
    )


class ASANegativeKeyword(TimestampMixin, Base):
    """Negative keyword scoped to either a campaign or an ad group.

    Exactly one of `campaign_id` / `ad_group_id` is non-null. Scope is
    derived from whichever is set — there is no separate `scope` column,
    keeping the source of truth in the FK pair.
    """

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
    asa_negative_keyword_id: Mapped[int]
    text: Mapped[str] = mapped_column(String(255))
    match_type: Mapped[str] = mapped_column(String(16))

    campaign: Mapped[ASACampaign | None] = relationship(
        back_populates="negative_keywords",
        foreign_keys=[campaign_id],
    )
    ad_group: Mapped[ASAAdGroup | None] = relationship(
        back_populates="negative_keywords",
        foreign_keys=[ad_group_id],
    )

    @property
    def scope(self) -> str:
        """Derived: CAMPAIGN if campaign_id is set, else AD_GROUP."""
        return "CAMPAIGN" if self.campaign_id is not None else "AD_GROUP"

    __table_args__ = (
        CheckConstraint(
            "(campaign_id IS NOT NULL AND ad_group_id IS NULL) OR "
            "(campaign_id IS NULL AND ad_group_id IS NOT NULL)",
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

    ad_group: Mapped[ASAAdGroup] = relationship(back_populates="search_terms")

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
    without joining the dim hierarchy. `date` is a calendar day in the org's
    timezone, not a timestamp — using `Date` preserves uniqueness on the grain.
    """

    __tablename__ = "asa_metric_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dim_kind: Mapped[str] = mapped_column(String(16))  # CAMPAIGN|AD_GROUP|KEYWORD|SEARCH_TERM
    dim_id: Mapped[int] = mapped_column(index=True)
    app_adam_id: Mapped[str] = mapped_column(String(32), index=True)
    # Tenant-scoping: ties each metric row to the credential (and thus user)
    # that synced it. Nullable so backfill can't fail on orphan rows; analytics
    # queries fail closed on NULL (a NULL-credential row is invisible to all).
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date)
    storefront: Mapped[str | None] = mapped_column(String(8), nullable=True)
    impressions: Mapped[int] = mapped_column(default=0)
    taps: Mapped[int] = mapped_column(default=0)
    installs: Mapped[int] = mapped_column(default=0)
    new_downloads: Mapped[int] = mapped_column(default=0)
    redownloads: Mapped[int] = mapped_column(default=0)
    spend_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    spend_currency: Mapped[str] = mapped_column(String(3))
    avg_cpa_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    avg_cpt_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    ttr: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)

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
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    credential: Mapped[ASACredential] = relationship()
