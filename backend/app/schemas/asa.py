"""Pydantic schemas for the Apple Search Ads vertical.

These mirror the SQLAlchemy models in `app.models.asa`. Reports and the
paid+organic join row are derived shapes returned by the service / API
layer; they are not bound to a single ORM table.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------- credentials ----------


class ASACredentialCreate(BaseModel):
    """Inbound payload for POST /asa/credentials.

    The three secrets (clientId, teamId, .p8 private key) are sent in the
    clear over TLS and immediately Fernet-encrypted server-side via
    `app.core.security.encrypt_value`.
    """

    name: str = Field(min_length=1, max_length=120)
    client_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1, max_length=64)
    private_key_pem: str = Field(
        min_length=1,
        description="Contents of the ASA .p8 private key file, PEM-encoded.",
    )


class ASACredentialOut(BaseModel):
    """Safe view of a credential row — never includes secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_id: str
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ASATestResult(BaseModel):
    """Result of POST /asa/credentials/{id}/test — a no-cost auth check."""

    ok: bool
    orgs_visible: int = 0
    detail: str | None = None


# ---------- dimensions ----------


class ASAOrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    credential_id: int
    asa_org_id: int
    name: str
    currency: str
    timezone: str
    role: str | None = None


class ASACampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    asa_campaign_id: int
    app_id: int | None = None
    app_adam_id: str
    name: str
    status: str
    supply_sources: list[Any] | None = None
    daily_budget_amount: Decimal | None = None
    daily_budget_currency: str | None = None
    storefronts: list[Any] | None = None
    archived_at: datetime | None = None


class ASAAdGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    asa_ad_group_id: int
    name: str
    status: str
    default_bid_amount: Decimal | None = None
    default_bid_currency: str | None = None
    age_range: dict[str, Any] | None = None
    gender: str | None = None
    device_class: str | None = None
    archived_at: datetime | None = None


class ASAKeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ad_group_id: int
    asa_keyword_id: int
    text: str
    match_type: str  # BROAD | EXACT
    bid_amount: Decimal | None = None
    bid_currency: str | None = None
    status: str
    archived_at: datetime | None = None


class ASANegativeKeywordOut(BaseModel):
    """`scope` is read from the model's derived property (the column was
    dropped in favor of deriving from which FK is non-null)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None = None
    ad_group_id: int | None = None
    asa_negative_keyword_id: int
    text: str
    match_type: Literal["BROAD", "EXACT"]
    scope: Literal["CAMPAIGN", "AD_GROUP"]


class ASASearchTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ad_group_id: int
    text: str
    match_type: str
    source: str  # SEARCHTERM | RAW
    archived_at: datetime | None = None


# ---------- metrics & reports ----------


class ASAMetricRow(BaseModel):
    """One row of the metric_daily fact table or an aggregated equivalent."""

    model_config = ConfigDict(from_attributes=True)

    dim_kind: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD", "SEARCH_TERM"]
    dim_id: int
    app_adam_id: str
    date: date
    storefront: str | None = None
    impressions: int = 0
    taps: int = 0
    installs: int = 0
    new_downloads: int = 0
    redownloads: int = 0
    spend_amount: Decimal = Decimal("0")
    spend_currency: str
    avg_cpa_amount: Decimal | None = None
    avg_cpt_amount: Decimal | None = None
    ttr: Decimal | None = None
    conversion_rate: Decimal | None = None


class ASAPerformanceReportOut(BaseModel):
    """Aggregated paid performance over a window, at one dimension grain."""

    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"]
    time_range: dict[str, str]  # {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    rows: list[ASAMetricRow]


class ASASearchTermReportOut(BaseModel):
    """Search-term report joined with metrics for an app's ad groups."""

    time_range: dict[str, str]
    rows: list[dict[str, Any]]


class PaidOrganicJoinRow(BaseModel):
    """One row joining a tracked organic keyword with its 30-day paid metrics.

    `term` and `organic_rank` come from `keyword_tracking`. `paid_*_30d`
    are summed from `asa_metric_daily` over the window. Paid columns are
    zero when the term has no matching ASA keyword (or zero traffic).
    """

    term: str
    organic_rank: int | None = None
    paid_impressions_30d: int = 0
    paid_taps_30d: int = 0
    paid_installs_30d: int = 0
    paid_spend_30d: Decimal = Decimal("0")
    paid_spend_currency: str | None = None


# ---------- mutations ----------


class NegativeKeywordIn(BaseModel):
    """One negative keyword in a bulk-add request."""

    text: str = Field(min_length=1, max_length=255)
    match_type: Literal["BROAD", "EXACT"]


class AddNegativeKeywordsRequest(BaseModel):
    """Bulk-add negatives at either CAMPAIGN or AD_GROUP scope.

    `scope_id` is the local row id of the campaign or ad group (not the
    Apple-side id). The downstream handler resolves the org and Apple ids
    from there.
    """

    scope: Literal["CAMPAIGN", "AD_GROUP"]
    scope_id: int
    keywords: list[NegativeKeywordIn] = Field(min_length=1, max_length=200)


# ---------- sync ops ----------


class ASASyncOperationOut(BaseModel):
    """Per-step result of `asa.sync` — mirrors the CloneOperation pattern."""

    id: int
    credential_id: int
    status: str
    full_backfill: bool
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error_log: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
