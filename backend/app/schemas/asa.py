"""Pydantic schemas for the Apple Search Ads vertical.

These mirror the SQLAlchemy models in `app.models.asa`. Reports and the
paid+organic join row are derived shapes returned by the service / API
layer; they are not bound to a single ORM table.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

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
    message: str
    orgs_found: int = 0


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
    supply_sources_json: list[Any] | None = None
    daily_budget_amount: Decimal | None = None
    daily_budget_currency: str | None = None
    storefronts_json: list[Any] | None = None
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
    age_range_json: dict[str, Any] | None = None
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
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None = None
    ad_group_id: int | None = None
    asa_negative_keyword_id: int
    text: str
    match_type: str
    scope: str  # CAMPAIGN | AD_GROUP


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

    dim_kind: str  # CAMPAIGN | AD_GROUP | KEYWORD | SEARCH_TERM
    dim_id: int
    app_adam_id: str
    date: datetime
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
    """Aggregated paid performance over a window, grouped by `group_by`."""

    group_by: str  # campaign | ad_group | keyword
    start_date: datetime
    end_date: datetime
    rows: list[ASAMetricRow]


class ASASearchTermReportOut(BaseModel):
    """Search-term report for an ad group or campaign."""

    ad_group_id: int | None = None
    campaign_id: int | None = None
    start_date: datetime
    end_date: datetime
    rows: list[ASAMetricRow]


class PaidOrganicJoinRow(BaseModel):
    """One row of the keyword × storefront paid+organic join.

    `paid_*` columns come from ASAMetricDaily (KEYWORD grain).
    `organic_*` columns come from the visibility tracker / KeywordRanking.
    Either side may be NULL when only one source has data for the pair.
    """

    keyword: str
    match_type: str | None = None
    storefront: str
    # paid side
    paid_impressions: int | None = None
    paid_taps: int | None = None
    paid_installs: int | None = None
    paid_spend_amount: Decimal | None = None
    paid_spend_currency: str | None = None
    paid_avg_cpa: Decimal | None = None
    paid_avg_cpt: Decimal | None = None
    # organic side
    organic_rank: int | None = None
    organic_visibility_pct: Decimal | None = None


# ---------- mutations ----------


class NegativeKeywordIn(BaseModel):
    """One negative keyword in a bulk-add request."""

    text: str = Field(min_length=1, max_length=255)
    match_type: str = Field(pattern="^(BROAD|EXACT)$")


class AddNegativeKeywordsRequest(BaseModel):
    """Bulk-add negatives to either a campaign or an ad group (XOR)."""

    campaign_id: int | None = None
    ad_group_id: int | None = None
    keywords: list[NegativeKeywordIn] = Field(min_length=1)


# ---------- sync ops ----------


class ASASyncOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    credential_id: int
    user_id: int
    status: str
    full_backfill: bool
    steps_json: list[Any] | None = None
    error_log_json: list[Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
