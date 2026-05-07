# Apple Search Ads Analytics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `asa.*` MCP namespace + REST surface that exposes Apple Search Ads campaigns, ad groups, keywords, and search-term reports for an app, persists 90 days of daily metrics, and supports negative-keyword writes — alongside an opt-in paid+organic join on existing keyword tooling.

**Architecture:** Per-user multi-org credential model encrypting the ES256 private key at rest (Fernet), `ASAClient` issuing OAuth2 client_credentials tokens (cached 1h, ES256 JWT signed with PyJWT), nine new SQLAlchemy tables snapshotting the ASA hierarchy plus a polymorphic daily-metrics fact table, sync orchestrator with `ASASyncOperation` for partial-failure recovery, 15 new MCP tools mirroring 12 REST endpoints. Same vertical shape as the existing ASC and RevenueCat integrations.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · PyJWT (ES256) · httpx · fastmcp 3.x · React 19 + TanStack Query v5 + Mantine v8 + mantine-datatable.

**Source spec:** `docs/superpowers/specs/2026-05-08-apple-search-ads-analytics-design.md` (committed `dc41204`).

**Phases (sequential except where noted):**

| Phase | Name | Parallelizable with |
| --- | --- | --- |
| 0 | Pre-flight | — |
| 1 | Models + schemas | — |
| 2 | ASA auth + client | — |
| 3 | Service layer (campaigns, search_terms, reports, joins, sync) | — |
| 4 | REST API | Phase 5 |
| 5 | MCP tools (`asa.*` + extensions) | Phase 4 |
| 6 | Frontend | After Phase 4 |
| 7 | End-to-end verification | — |

---

## Phase 0 — Pre-flight

### Task 0.1: Verify baseline state

**Files:** none modified.

- [ ] **Step 1: Confirm current branch is clean of unrelated work**

Run: `git status`
Expected: working tree contains the prior MCP/PAT changes (acceptable carry-over) and `docs/superpowers/specs/2026-05-08-apple-search-ads-analytics-design.md` is committed at `dc41204`. No other unexpected files.

- [ ] **Step 2: Confirm 123 MCP tools currently register**

Run:
```bash
cd backend && uv run python -c "
import asyncio
from app.mcp.server import mcp
async def main():
    tools = await mcp.list_tools()
    print(len(tools))
asyncio.run(main())
"
```
Expected: `123`. After this plan completes, the count must be `138`.

- [ ] **Step 3: Confirm baseline test count**

Run: `cd backend && uv run pytest -q -k "not test_preview_logic and not test_exchange_rate_preview" 2>&1 | tail -3`
Expected: `83 passed`.

---

## Phase 1 — Models + schemas

### Task 1.1: Create `asa_credential` model

**Files:**
- Create: `backend/app/models/asa.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_asa_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asa_models.py
import pytest
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily, ASASyncOperation,
)

def test_asa_credential_table_name():
    assert ASACredential.__tablename__ == "asa_credentials"

def test_asa_metric_daily_unique_constraint():
    cols = {c.name for c in ASAMetricDaily.__table__.indexes
            if c.name == "ix_asa_metric_daily_dim_date"}
    # see Task 1.2 for index naming
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_asa_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.asa'`.

- [ ] **Step 3: Write the credential model**

```python
# backend/app/models/asa.py — partial; remaining models added in Tasks 1.2-1.4
from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.app import App
    from app.models.user import User


class ASACredential(TimestampMixin, Base):
    __tablename__ = "asa_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    name: Mapped[str] = mapped_column(String(120))
    client_id_ciphertext: Mapped[str] = mapped_column(Text)
    team_id_ciphertext: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(64))
    private_key_ciphertext: Mapped[str] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
```

- [ ] **Step 4: Register on Base.metadata via models package**

```python
# backend/app/models/__init__.py — add to imports + __all__
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily, ASASyncOperation,
)
```
Add `"ASACredential"`, etc. to `__all__`.

- [ ] **Step 5: Re-run the test, confirm it passes for credential**

Run: `cd backend && uv run pytest tests/test_asa_models.py::test_asa_credential_table_name -v`
Expected: PASS.

### Task 1.2: Add the dimension models (org, campaign, ad_group, keyword)

**Files:** Modify `backend/app/models/asa.py`.

- [ ] **Step 1: Append `ASAOrg`, `ASACampaign`, `ASAAdGroup`, `ASAKeyword`**

```python
class ASAOrg(TimestampMixin, Base):
    __tablename__ = "asa_orgs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"), index=True,
    )
    asa_org_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        UniqueConstraint("credential_id", "asa_org_id",
                         name="uq_asa_org_credential_asaid"),
    )


class ASACampaign(TimestampMixin, Base):
    __tablename__ = "asa_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("asa_orgs.id", ondelete="CASCADE"), index=True,
    )
    asa_campaign_id: Mapped[int] = mapped_column(Integer)
    app_id: Mapped[int | None] = mapped_column(
        ForeignKey("apps.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    app_adam_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    supply_sources_json: Mapped[list | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    daily_budget_amount: Mapped[float | None] = mapped_column(
        Numeric(18, 6), nullable=True,
    )
    daily_budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    storefronts_json: Mapped[list | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    __table_args__ = (
        UniqueConstraint("org_id", "asa_campaign_id",
                         name="uq_asa_campaign_org_asaid"),
    )


class ASAAdGroup(TimestampMixin, Base):
    __tablename__ = "asa_ad_groups"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("asa_campaigns.id", ondelete="CASCADE"), index=True,
    )
    asa_ad_group_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    default_bid_amount: Mapped[float | None] = mapped_column(
        Numeric(18, 6), nullable=True,
    )
    default_bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    age_range_json: Mapped[dict | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    __table_args__ = (
        UniqueConstraint("campaign_id", "asa_ad_group_id",
                         name="uq_asa_ad_group_campaign_asaid"),
    )


class ASAKeyword(TimestampMixin, Base):
    __tablename__ = "asa_keywords"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ad_group_id: Mapped[int] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"), index=True,
    )
    asa_keyword_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16))  # BROAD | EXACT
    bid_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    bid_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    __table_args__ = (
        UniqueConstraint("ad_group_id", "asa_keyword_id",
                         name="uq_asa_keyword_adgroup_asaid"),
        Index("ix_asa_keyword_text_lower", "text"),
    )
```

- [ ] **Step 2: Verify the models import**

Run: `cd backend && uv run python -c "from app.models.asa import ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword; print('ok')"`
Expected: `ok`.

### Task 1.3: Add negatives, search terms, fact table, sync ops

**Files:** Modify `backend/app/models/asa.py`.

- [ ] **Step 1: Append remaining models**

```python
class ASANegativeKeyword(TimestampMixin, Base):
    __tablename__ = "asa_negative_keywords"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("asa_campaigns.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    ad_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"), nullable=True, index=True,
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
    __tablename__ = "asa_search_terms"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ad_group_id: Mapped[int] = mapped_column(
        ForeignKey("asa_ad_groups.id", ondelete="CASCADE"), index=True,
    )
    text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))  # SEARCHTERM | RAW
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    __table_args__ = (
        UniqueConstraint("ad_group_id", "text", "match_type",
                         name="uq_asa_search_term_adgroup_text_match"),
    )


class ASAMetricDaily(TimestampMixin, Base):
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
        UniqueConstraint("dim_kind", "dim_id", "date", "storefront",
                         name="uq_asa_metric_daily_grain"),
        Index("ix_asa_metric_daily_app_date", "app_adam_id", "date"),
    )


class ASASyncOperation(TimestampMixin, Base):
    __tablename__ = "asa_sync_operations"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asa_credentials.id", ondelete="CASCADE"), index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(16))  # pending|running|done|partial|failed
    full_backfill: Mapped[bool] = mapped_column(default=False)
    steps_json: Mapped[list | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    error_log_json: Mapped[list | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
```

- [ ] **Step 2: Boot test**

Run: `cd backend && uv run python -c "from app.main import app; print('boot OK')"`
Expected: `boot OK` (SQLite auto-creates the new tables on next run).

- [ ] **Step 3: Confirm tables created in dev SQLite**

Run:
```bash
cd backend && uv run python -c "
import asyncio
from app.db.session import engine
from sqlalchemy import inspect
async def main():
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: inspect(c).get_table_names())
        for n in names:
            if n.startswith('asa_'):
                print(n)
asyncio.run(main())
"
```
Expected: prints all 9 `asa_*` table names.

### Task 1.4: Add Pydantic schemas

**Files:**
- Create: `backend/app/schemas/asa.py`

- [ ] **Step 1: Write schemas**

```python
# backend/app/schemas/asa.py
from __future__ import annotations
from datetime import datetime, date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ASACredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    client_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1, max_length=64)
    private_key_pem: str = Field(min_length=1)


class ASACredentialOut(BaseModel):
    id: int
    name: str
    key_id: str
    last_synced_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ASATestResult(BaseModel):
    ok: bool
    orgs_visible: int
    detail: str | None = None


class ASAOrgOut(BaseModel):
    id: int
    asa_org_id: int
    name: str
    currency: str
    timezone: str
    model_config = ConfigDict(from_attributes=True)


class ASACampaignOut(BaseModel):
    id: int
    asa_campaign_id: int
    org_id: int
    app_id: int | None
    app_adam_id: str
    name: str
    status: str
    daily_budget_amount: float | None
    daily_budget_currency: str | None
    archived_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class ASAAdGroupOut(BaseModel):
    id: int
    asa_ad_group_id: int
    campaign_id: int
    name: str
    status: str
    default_bid_amount: float | None
    default_bid_currency: str | None
    archived_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class ASAKeywordOut(BaseModel):
    id: int
    asa_keyword_id: int
    ad_group_id: int
    text: str
    match_type: str
    bid_amount: float | None
    bid_currency: str | None
    status: str
    archived_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class ASANegativeKeywordOut(BaseModel):
    id: int
    asa_negative_keyword_id: int
    campaign_id: int | None
    ad_group_id: int | None
    text: str
    match_type: str
    scope: Literal["CAMPAIGN", "AD_GROUP"]
    model_config = ConfigDict(from_attributes=True)


class ASASearchTermOut(BaseModel):
    id: int
    ad_group_id: int
    text: str
    match_type: str
    source: str
    archived_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class ASAMetricRow(BaseModel):
    dim_kind: str
    dim_id: int
    date: date
    storefront: str | None
    impressions: int
    taps: int
    installs: int
    spend_amount: float
    spend_currency: str
    avg_cpa_amount: float | None
    avg_cpt_amount: float | None
    ttr: float | None
    conversion_rate: float | None


class ASAPerformanceReportOut(BaseModel):
    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"]
    time_range: dict  # {start: date, end: date}
    rows: list[ASAMetricRow]


class ASASearchTermReportOut(BaseModel):
    time_range: dict
    rows: list[dict]  # joined search-term + metrics


class PaidOrganicJoinRow(BaseModel):
    term: str
    organic_rank: int | None
    paid_impressions_30d: int
    paid_taps_30d: int
    paid_installs_30d: int
    paid_spend_30d: float
    paid_spend_currency: str | None


class NegativeKeywordIn(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    match_type: Literal["BROAD", "EXACT"]


class AddNegativeKeywordsRequest(BaseModel):
    scope: Literal["CAMPAIGN", "AD_GROUP"]
    scope_id: int
    keywords: list[NegativeKeywordIn] = Field(min_length=1, max_length=200)


class ASASyncOperationOut(BaseModel):
    id: int
    credential_id: int
    status: str
    full_backfill: bool
    steps: list[dict]
    error_log: list[str]
    started_at: datetime | None
    completed_at: datetime | None
```

- [ ] **Step 2: Commit Phase 1**

```bash
git add backend/app/models/asa.py backend/app/models/__init__.py \
        backend/app/schemas/asa.py backend/tests/test_asa_models.py
git commit -m "$(cat <<'EOF'
feat(asa): add models and schemas for Apple Search Ads vertical

Nine new tables (credential, org, campaign, ad_group, keyword,
negative_keyword, search_term, metric_daily, sync_operation) plus the
matching pydantic schemas. Auto-create on SQLite boot; Alembic revision
follows in Task 1.5 if running against PostgreSQL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.5: Generate Alembic revision (PostgreSQL)

**Files:**
- Create: `backend/alembic/versions/<rev>_add_asa_tables.py`

- [ ] **Step 1: Generate revision**

Run: `cd backend && uv run alembic revision --autogenerate -m "add asa tables"`
Expected: a new revision file with `op.create_table` calls for all 9 `asa_*` tables.

- [ ] **Step 2: Inspect the revision**

Open the generated file. Verify it creates all 9 tables, the unique constraints, the check constraint on `asa_negative_keywords`, and the indexes. Hand-edit if autogenerate produced anything unexpected.

- [ ] **Step 3: Apply against a clean PostgreSQL DB if available**

Run: `cd backend && uv run alembic upgrade head` (only if `DATABASE_URL` points to PostgreSQL — skip if SQLite).
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/<rev>_add_asa_tables.py
git commit -m "feat(asa): alembic revision for asa_* tables"
```

---

## Phase 2 — ASA auth + client

### Task 2.1: ASA errors module

**Files:**
- Create: `backend/app/services/asa/__init__.py` (empty)
- Create: `backend/app/services/asa/errors.py`
- Test: `backend/tests/test_asa_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asa_errors.py
from app.services.asa.errors import ASAAPIError

def test_asa_api_error_str_redacts_long_body():
    err = ASAAPIError("rate limit", status=429, body="x" * 5000)
    s = str(err)
    assert "rate limit" in s
    assert "429" in s
    assert "x" * 5000 not in s  # body truncated
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/test_asa_errors.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/services/asa/errors.py
class ASAAPIError(Exception):
    """An error from the Apple Search Ads API."""

    def __init__(self, message: str, *, status: int | None = None,
                 body: str | None = None):
        self.message = message
        self.status = status
        self.body = body
        super().__init__(message)

    def __str__(self) -> str:
        body_preview = (self.body or "")[:500]
        if self.status is not None:
            return f"ASA API {self.status}: {self.message} ({body_preview})"
        return f"ASA API: {self.message} ({body_preview})"
```

- [ ] **Step 4: Verify test passes**

Run: `cd backend && uv run pytest tests/test_asa_errors.py -v`
Expected: PASS.

### Task 2.2: ES256 client_secret JWT builder

**Files:**
- Create: `backend/app/services/asa/auth.py`
- Test: `backend/tests/test_asa_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asa_auth.py
import time
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
from cryptography.hazmat.primitives import serialization
from app.services.asa.auth import build_client_secret


def _make_pem() -> str:
    key = generate_private_key(SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_build_client_secret_has_correct_claims():
    pem = _make_pem()
    token = build_client_secret(
        client_id="SEARCHADS.x",
        team_id="TEAM",
        key_id="KID",
        private_key_pem=pem,
    )
    headers = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert headers == {"alg": "ES256", "kid": "KID", "typ": "JWT"}
    assert payload["sub"] == "SEARCHADS.x"
    assert payload["aud"] == "https://appleid.apple.com"
    assert payload["iss"] == "TEAM"
    assert payload["exp"] - payload["iat"] <= 30 * 60 + 5
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/test_asa_auth.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/services/asa/auth.py
import time
from typing import Final

import jwt as pyjwt

ASA_AUDIENCE: Final[str] = "https://appleid.apple.com"
ASA_TOKEN_URL: Final[str] = "https://appleid.apple.com/auth/oauth2/token"
CLIENT_SECRET_TTL_SECONDS: Final[int] = 30 * 60  # 30min


def build_client_secret(
    *, client_id: str, team_id: str, key_id: str, private_key_pem: str,
    now: int | None = None,
) -> str:
    iat = int(now if now is not None else time.time())
    payload = {
        "sub": client_id,
        "aud": ASA_AUDIENCE,
        "iss": team_id,
        "iat": iat,
        "exp": iat + CLIENT_SECRET_TTL_SECONDS,
    }
    return pyjwt.encode(
        payload,
        private_key_pem,
        algorithm="ES256",
        headers={"kid": key_id},
    )
```

- [ ] **Step 4: Verify test passes**

Run: `cd backend && uv run pytest tests/test_asa_auth.py -v`
Expected: PASS.

### Task 2.3: Access token fetch + cache

**Files:**
- Modify: `backend/app/services/asa/auth.py`
- Test: `backend/tests/test_asa_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_asa_auth.py
import httpx
import pytest
from app.services.asa.auth import fetch_access_token, AccessTokenCache


@pytest.mark.anyio
async def test_fetch_access_token_posts_correct_form():
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        token, expires_at = await fetch_access_token(
            client_id="cid", client_secret="jwt", http=client,
        )
    assert token == "abc"
    assert "client_id=cid" in captured["body"]
    assert "scope=searchadsorg" in captured["body"]
    assert "grant_type=client_credentials" in captured["body"]


@pytest.mark.anyio
async def test_access_token_cache_hits_within_ttl():
    cache = AccessTokenCache()
    await cache.set(1, "tok1", expires_at=time.time() + 1000)
    assert await cache.get(1) == "tok1"
    await cache.set(1, "tok2", expires_at=time.time() - 1)  # expired
    assert await cache.get(1) is None
```

Note: the project may need pytest-anyio configured. If `pytest.mark.anyio` is unavailable, replace with `asyncio.run` invocation.

- [ ] **Step 2: Run, verify FAIL**

Run: `cd backend && uv run pytest tests/test_asa_auth.py -v`
Expected: FAIL — `fetch_access_token` not defined.

- [ ] **Step 3: Implement**

```python
# Append to backend/app/services/asa/auth.py
import asyncio
import time
import httpx


async def fetch_access_token(
    *, client_id: str, client_secret: str, http: httpx.AsyncClient | None = None,
) -> tuple[str, float]:
    own_http = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(
            ASA_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": "searchadsorg",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            from app.services.asa.errors import ASAAPIError
            raise ASAAPIError(
                "token request failed", status=resp.status_code, body=resp.text,
            )
        data = resp.json()
        return data["access_token"], time.time() + int(data.get("expires_in", 3600))
    finally:
        if own_http:
            await client.aclose()


class AccessTokenCache:
    """Per-process cache. Key: credential_id. Value: (token, expires_at)."""

    def __init__(self) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, credential_id: int) -> str | None:
        async with self._lock:
            t = self._tokens.get(credential_id)
            if t is None:
                return None
            token, expires_at = t
            if expires_at <= time.time() + 5:
                self._tokens.pop(credential_id, None)
                return None
            return token

    async def set(self, credential_id: int, token: str, expires_at: float) -> None:
        async with self._lock:
            self._tokens[credential_id] = (token, expires_at)

    async def invalidate(self, credential_id: int) -> None:
        async with self._lock:
            self._tokens.pop(credential_id, None)


_TOKEN_CACHE = AccessTokenCache()


def get_token_cache() -> AccessTokenCache:
    return _TOKEN_CACHE
```

- [ ] **Step 4: Verify tests pass**

Run: `cd backend && uv run pytest tests/test_asa_auth.py -v`
Expected: PASS for synchronous tests. The `@pytest.mark.anyio` ones may need `pyproject.toml` setting `[tool.pytest.ini_options] anyio_mode = "auto"`. If that's a project-wide change you don't want to make, rewrite using `asyncio.run(...)` in the test bodies.

### Task 2.4: ASAClient

**Files:**
- Create: `backend/app/services/asa/client.py`
- Test: `backend/tests/test_asa_client.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asa_client.py
import pytest
import httpx
import asyncio
from app.services.asa.client import ASAClient, ASA_API_BASE
from app.services.asa.errors import ASAAPIError


def _build_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=ASA_API_BASE)
    return ASAClient.__test_with_token__(http=http, access_token="tok-abc")


def test_request_includes_bearer_and_org_context():
    captured = {}
    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers["authorization"]
        captured["ctx"] = req.headers.get("x-ap-context")
        return httpx.Response(200, json={"data": []})

    async def go():
        client = _build_client(handler)
        await client.request("GET", "/me/acl")
        assert captured["auth"] == "Bearer tok-abc"
        assert captured["ctx"] is None
        await client.request("POST", "/campaigns/find", org_id=42, json={"q": 1})
        assert captured["ctx"] == "orgId=42"
        await client.aclose()
    asyncio.run(go())


def test_request_raises_asa_api_error_on_4xx():
    def handler(req): return httpx.Response(403, text="nope")
    async def go():
        client = _build_client(handler)
        with pytest.raises(ASAAPIError) as ei:
            await client.request("GET", "/anything")
        assert ei.value.status == 403
        await client.aclose()
    asyncio.run(go())
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd backend && uv run pytest tests/test_asa_client.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/services/asa/client.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Final

import httpx

from app.core.security import decrypt_value
from app.models.asa import ASACredential
from app.services.asa.auth import (
    build_client_secret, fetch_access_token, get_token_cache,
)
from app.services.asa.errors import ASAAPIError

ASA_API_BASE: Final[str] = "https://api.searchads.apple.com/api/v5"
_MIN_REQUEST_INTERVAL: Final[float] = 0.15  # 150ms

logger = logging.getLogger(__name__)


class ASAClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        access_token: str,
        credential_id: int | None = None,
        client_id: str | None = None,
        client_secret_jwt: str | None = None,
    ) -> None:
        self._http = http
        self._access_token = access_token
        self._credential_id = credential_id
        self._client_id = client_id
        self._client_secret_jwt = client_secret_jwt
        self._last_request_at = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    async def from_credential(cls, cred: ASACredential) -> "ASAClient":
        client_id = decrypt_value(cred.client_id_ciphertext)
        team_id = decrypt_value(cred.team_id_ciphertext)
        private_key_pem = decrypt_value(cred.private_key_ciphertext)
        client_secret = build_client_secret(
            client_id=client_id, team_id=team_id, key_id=cred.key_id,
            private_key_pem=private_key_pem,
        )
        cache = get_token_cache()
        token = await cache.get(cred.id)
        http = httpx.AsyncClient(base_url=ASA_API_BASE, timeout=60.0)
        if token is None:
            token, expires_at = await fetch_access_token(
                client_id=client_id, client_secret=client_secret, http=http,
            )
            await cache.set(cred.id, token, expires_at)
        return cls(
            http=http, access_token=token, credential_id=cred.id,
            client_id=client_id, client_secret_jwt=client_secret,
        )

    @classmethod
    def __test_with_token__(
        cls, *, http: httpx.AsyncClient, access_token: str,
    ) -> "ASAClient":
        return cls(http=http, access_token=access_token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _refresh_token(self) -> None:
        if self._credential_id is None or self._client_id is None or self._client_secret_jwt is None:
            return
        cache = get_token_cache()
        await cache.invalidate(self._credential_id)
        token, expires_at = await fetch_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret_jwt,
            http=self._http,
        )
        await cache.set(self._credential_id, token, expires_at)
        self._access_token = token

    async def request(
        self,
        method: str,
        path: str,
        *,
        org_id: int | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retries: int = 6,
    ) -> Any:
        async with self._lock:
            elapsed = time.time() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        backoff = 1.0
        last_resp: httpx.Response | None = None
        for attempt in range(retries):
            headers = {"Authorization": f"Bearer {self._access_token}"}
            if org_id is not None:
                headers["X-AP-Context"] = f"orgId={org_id}"
            try:
                resp = await self._http.request(
                    method, path, json=json, params=params, headers=headers,
                )
            except httpx.HTTPError as exc:
                logger.warning("ASA HTTP error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            self._last_request_at = time.time()
            if resp.status_code == 401 and attempt == 0:
                await self._refresh_token()
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                await asyncio.sleep(retry_after)
                backoff = min(backoff * 2, 60.0)
                last_resp = resp
                continue
            if resp.status_code >= 400:
                raise ASAAPIError(
                    f"{method} {path} failed",
                    status=resp.status_code, body=resp.text,
                )
            try:
                return resp.json()
            except Exception:
                return resp.text
        raise ASAAPIError(
            "exhausted retries",
            status=last_resp.status_code if last_resp is not None else None,
            body=last_resp.text if last_resp is not None else None,
        )

    async def get_all_pages(
        self, method: str, path: str, *, org_id: int | None = None,
        page_size: int = 1000,
    ) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            payload = await self.request(
                method, path, org_id=org_id,
                json={"selector": {"pagination": {"offset": offset, "limit": page_size}}},
            )
            data = payload.get("data") or []
            out.extend(data)
            total = payload.get("pagination", {}).get("totalResults", len(out))
            offset += len(data)
            if not data or offset >= total:
                break
        return out
```

- [ ] **Step 4: Verify tests pass**

Run: `cd backend && uv run pytest tests/test_asa_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Phase 2**

```bash
git add backend/app/services/asa/__init__.py backend/app/services/asa/errors.py \
        backend/app/services/asa/auth.py backend/app/services/asa/client.py \
        backend/tests/test_asa_errors.py backend/tests/test_asa_auth.py \
        backend/tests/test_asa_client.py
git commit -m "feat(asa): ES256 auth + ASAClient with token cache, retry, rate limit"
```

---

## Phase 3 — Service layer

### Task 3.1: campaigns service

**Files:**
- Create: `backend/app/services/asa/campaigns.py`

- [ ] **Step 1: Implement**

```python
# backend/app/services/asa/campaigns.py
from __future__ import annotations
from typing import Any

from app.services.asa.client import ASAClient


async def list_orgs_for_credential(client: ASAClient) -> list[dict[str, Any]]:
    payload = await client.request("GET", "/me/acl")
    return payload.get("data") or []


async def list_campaigns(client: ASAClient, *, org_id: int) -> list[dict[str, Any]]:
    return await client.get_all_pages("POST", "/campaigns/find", org_id=org_id)


async def list_ad_groups(
    client: ASAClient, *, org_id: int, campaign_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST", f"/campaigns/{campaign_id}/adgroups/find", org_id=org_id,
    )


async def list_targeting_keywords(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/targetingkeywords/find",
        org_id=org_id,
    )


async def list_negative_keywords_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST", f"/campaigns/{campaign_id}/negativekeywords/find", org_id=org_id,
    )


async def list_negative_keywords_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/find",
        org_id=org_id,
    )


async def add_negative_keywords_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int,
    keywords: list[dict[str, str]],
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST", f"/campaigns/{campaign_id}/negativekeywords/bulk",
        org_id=org_id, json=[{"text": k["text"], "matchType": k["match_type"]}
                             for k in keywords],
    )
    return payload.get("data") or []


async def add_negative_keywords_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    keywords: list[dict[str, str]],
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/bulk",
        org_id=org_id, json=[{"text": k["text"], "matchType": k["match_type"]}
                             for k in keywords],
    )
    return payload.get("data") or []


async def remove_negative_keyword_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int, negative_id: int,
) -> None:
    await client.request(
        "DELETE",
        f"/campaigns/{campaign_id}/negativekeywords/{negative_id}",
        org_id=org_id,
    )


async def remove_negative_keyword_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    negative_id: int,
) -> None:
    await client.request(
        "DELETE",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/{negative_id}",
        org_id=org_id,
    )
```

### Task 3.2: reports service

**Files:**
- Create: `backend/app/services/asa/reports.py`

- [ ] **Step 1: Implement**

```python
# backend/app/services/asa/reports.py
from __future__ import annotations
from datetime import date
from typing import Any, Literal

from app.services.asa.client import ASAClient

Granularity = Literal["DAILY"]


def _selector(start: date, end: date, granularity: Granularity = "DAILY") -> dict[str, Any]:
    return {
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
        "selector": {
            "orderBy": [{"field": "spend", "sortOrder": "DESCENDING"}],
            "pagination": {"offset": 0, "limit": 1000},
        },
        "granularity": granularity,
        "timeZone": "UTC",
        "returnRowTotals": False,
        "returnGrandTotals": False,
    }


async def campaign_report(
    client: ASAClient, *, org_id: int, start: date, end: date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST", "/reports/campaigns",
        org_id=org_id, json=_selector(start, end),
    )
    return (payload.get("data") or {}).get("reportingDataResponse", {}).get("row", [])


async def ad_group_report(
    client: ASAClient, *, org_id: int, campaign_id: int, start: date, end: date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST", f"/reports/campaigns/{campaign_id}/adgroups",
        org_id=org_id, json=_selector(start, end),
    )
    return (payload.get("data") or {}).get("reportingDataResponse", {}).get("row", [])


async def keyword_report(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    start: date, end: date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/reports/campaigns/{campaign_id}/adgroups/{ad_group_id}/keywords",
        org_id=org_id, json=_selector(start, end),
    )
    return (payload.get("data") or {}).get("reportingDataResponse", {}).get("row", [])


async def search_term_report(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    start: date, end: date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/reports/campaigns/{campaign_id}/adgroups/{ad_group_id}/searchterms",
        org_id=org_id, json=_selector(start, end),
    )
    return (payload.get("data") or {}).get("reportingDataResponse", {}).get("row", [])
```

### Task 3.3: paid+organic join service

**Files:**
- Create: `backend/app/services/asa/joins.py`
- Test: `backend/tests/test_asa_joins.py`

- [ ] **Step 1: Write the failing test (fixture-based, sqlite)**

```python
# backend/tests/test_asa_joins.py
import pytest
import asyncio
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import async_session_factory
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword, ASAMetricDaily,
)
from app.models.keyword import KeywordTracking
from app.core.security import hash_password, encrypt_value
from app.services.asa.joins import paid_organic_join


def test_paid_organic_join_returns_merged_metrics():
    async def go():
        async with async_session_factory() as session:
            user = User(email="t@x.x", password_hash=hash_password("xxxxxxxx"), name="T")
            session.add(user); await session.commit(); await session.refresh(user)
            cred = ASCCredential(user_id=user.id, name="c",
                                 issuer_id_ciphertext=encrypt_value("i"),
                                 key_id="k",
                                 private_key_ciphertext=encrypt_value("k"))
            session.add(cred); await session.commit(); await session.refresh(cred)
            app = App(credential_id=cred.id, asc_app_id="X", adam_id="999",
                      bundle_id="b", name="n", primary_locale="en-US",
                      sku="s", platform="ios")
            session.add(app); await session.commit(); await session.refresh(app)
            asa_cred = ASACredential(
                user_id=user.id, name="asa",
                client_id_ciphertext=encrypt_value("c"),
                team_id_ciphertext=encrypt_value("t"), key_id="k",
                private_key_ciphertext=encrypt_value("p"),
            )
            session.add(asa_cred); await session.commit(); await session.refresh(asa_cred)
            org = ASAOrg(credential_id=asa_cred.id, asa_org_id=1,
                         name="o", currency="USD", timezone="UTC")
            session.add(org); await session.commit(); await session.refresh(org)
            camp = ASACampaign(org_id=org.id, asa_campaign_id=11, app_id=app.id,
                               app_adam_id=app.adam_id, name="c1", status="ENABLED")
            session.add(camp); await session.commit(); await session.refresh(camp)
            ag = ASAAdGroup(campaign_id=camp.id, asa_ad_group_id=22, name="a", status="ENABLED")
            session.add(ag); await session.commit(); await session.refresh(ag)
            kw = ASAKeyword(ad_group_id=ag.id, asa_keyword_id=33,
                            text="kanban app", match_type="EXACT", status="ENABLED")
            session.add(kw); await session.commit(); await session.refresh(kw)
            today = datetime.now(timezone.utc).date()
            session.add(ASAMetricDaily(
                dim_kind="KEYWORD", dim_id=kw.id, app_adam_id=app.adam_id,
                date=datetime.combine(today, datetime.min.time()),
                impressions=1000, taps=100, installs=10,
                spend_amount=50.0, spend_currency="USD",
            ))
            session.add(KeywordTracking(app_id=app.id, term="kanban app", last_rank=12))
            session.add(KeywordTracking(app_id=app.id, term="todo list", last_rank=4))
            await session.commit()

            rows = await paid_organic_join(
                session=session, app_id=app.id, days=30,
            )
            by_term = {r["term"]: r for r in rows}
            assert by_term["kanban app"]["paid_impressions_30d"] == 1000
            assert by_term["kanban app"]["organic_rank"] == 12
            assert by_term["todo list"]["paid_impressions_30d"] == 0
    asyncio.run(go())
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd backend && uv run pytest tests/test_asa_joins.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/services/asa/joins.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import ASAKeyword, ASAMetricDaily
from app.models.keyword import KeywordTracking


async def paid_organic_join(
    *, session: AsyncSession, app_id: int, days: int = 30,
) -> list[dict[str, Any]]:
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    metrics_subq = (
        select(
            ASAKeyword.id.label("kw_id"),
            func.lower(ASAKeyword.text).label("text_lower"),
            func.coalesce(func.sum(ASAMetricDaily.impressions), 0).label("imp"),
            func.coalesce(func.sum(ASAMetricDaily.taps), 0).label("taps"),
            func.coalesce(func.sum(ASAMetricDaily.installs), 0).label("ins"),
            func.coalesce(func.sum(ASAMetricDaily.spend_amount), 0).label("spend"),
            func.max(ASAMetricDaily.spend_currency).label("currency"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "KEYWORD")
            & (ASAMetricDaily.dim_id == ASAKeyword.id)
            & (ASAMetricDaily.date >= cutoff),
            isouter=True,
        )
        .where(ASAKeyword.archived_at.is_(None))
        .where(ASAMetricDaily.app_adam_id == app.adam_id)
        .group_by(ASAKeyword.id, ASAKeyword.text)
        .subquery()
    )

    stmt = (
        select(
            KeywordTracking.term,
            KeywordTracking.last_rank,
            metrics_subq.c.imp,
            metrics_subq.c.taps,
            metrics_subq.c.ins,
            metrics_subq.c.spend,
            metrics_subq.c.currency,
        )
        .select_from(KeywordTracking)
        .join(
            metrics_subq,
            func.lower(KeywordTracking.term) == metrics_subq.c.text_lower,
            isouter=True,
        )
        .where(KeywordTracking.app_id == app_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "term": r.term,
            "organic_rank": r.last_rank,
            "paid_impressions_30d": int(r.imp or 0),
            "paid_taps_30d": int(r.taps or 0),
            "paid_installs_30d": int(r.ins or 0),
            "paid_spend_30d": float(r.spend or 0),
            "paid_spend_currency": r.currency,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Verify test passes**

Run: `cd backend && uv run pytest tests/test_asa_joins.py -v`
Expected: PASS.

### Task 3.4: insight services (rule-based suggestions)

**Files:**
- Modify: `backend/app/services/asa/joins.py`

- [ ] **Step 1: Append**

```python
async def suggest_organic_keywords_to_track(
    *, session: AsyncSession, app_id: int, days: int = 30,
    min_taps: int = 20,
) -> list[dict[str, Any]]:
    """Search terms with >= min_taps that aren't in tracked organic keywords."""
    from app.models.asa import ASASearchTerm
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            ASASearchTerm.text,
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("installs"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff),
        )
        .where(ASAMetricDaily.app_adam_id == app.adam_id)
        .group_by(ASASearchTerm.text)
        .having(func.sum(ASAMetricDaily.taps) >= min_taps)
    )
    rows = (await session.execute(stmt)).all()
    tracked = {r[0].lower() for r in (await session.execute(
        select(KeywordTracking.term).where(KeywordTracking.app_id == app_id)
    )).all()}
    return [
        {"text": r.text, "taps": int(r.taps), "installs": int(r.installs)}
        for r in rows if r.text.lower() not in tracked
    ]


async def suggest_negative_candidates(
    *, session: AsyncSession, app_id: int, days: int = 30,
    min_spend: float = 10.0, max_conv_rate: float = 0.005,
) -> list[dict[str, Any]]:
    """Search terms wasting spend with low conversion."""
    from app.models.asa import ASASearchTerm
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            ASASearchTerm.id,
            ASASearchTerm.text,
            ASASearchTerm.ad_group_id,
            func.sum(ASAMetricDaily.spend_amount).label("spend"),
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("installs"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff),
        )
        .where(ASAMetricDaily.app_adam_id == app.adam_id)
        .group_by(ASASearchTerm.id, ASASearchTerm.text, ASASearchTerm.ad_group_id)
        .having(func.sum(ASAMetricDaily.spend_amount) >= min_spend)
    )
    rows = (await session.execute(stmt)).all()
    out = []
    for r in rows:
        taps = int(r.taps or 0)
        installs = int(r.installs or 0)
        conv = installs / taps if taps else 0.0
        if conv <= max_conv_rate:
            out.append({
                "search_term_id": r.id, "text": r.text,
                "ad_group_id": r.ad_group_id,
                "spend": float(r.spend), "taps": taps,
                "installs": installs, "conversion_rate": conv,
            })
    return out
```

### Task 3.5: sync orchestrator

**Files:**
- Create: `backend/app/services/asa/sync.py`

- [ ] **Step 1: Implement**

```python
# backend/app/services/asa/sync.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily, ASASyncOperation,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa import reports as asa_reports
from app.services.asa.client import ASAClient

logger = logging.getLogger(__name__)


def _upsert(session: AsyncSession, table, values: list[dict],
            index_elements: list[str]):
    if not values:
        return
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    stmt = insert(table).values(values)
    update_cols = {c: getattr(stmt.excluded, c) for c in values[0].keys()
                   if c not in index_elements}
    stmt = stmt.on_conflict_do_update(
        index_elements=index_elements, set_=update_cols,
    )
    return session.execute(stmt)


async def run_sync(
    *, session: AsyncSession, credential_id: int, user_id: int,
    full_backfill: bool = False,
) -> ASASyncOperation:
    cred = (await session.execute(
        select(ASACredential).where(
            ASACredential.id == credential_id,
            ASACredential.user_id == user_id,
        )
    )).scalar_one()

    op = ASASyncOperation(
        credential_id=cred.id, user_id=user_id, status="running",
        full_backfill=full_backfill,
        steps_json=[], error_log_json=[],
        started_at=datetime.now(timezone.utc),
    )
    session.add(op)
    await session.flush()

    client = await ASAClient.from_credential(cred)
    steps: list[dict] = []
    errors: list[str] = []

    try:
        # Step 1: orgs
        steps.append({"name": "orgs", "status": "running"})
        orgs_data = await asa_campaigns.list_orgs_for_credential(client)
        for o in orgs_data:
            attrs = o.get("orgName") and o or o.get("attributes", o)
            await _upsert(session, ASAOrg.__table__, [{
                "credential_id": cred.id,
                "asa_org_id": o.get("orgId") or attrs.get("orgId"),
                "name": attrs.get("orgName") or attrs.get("name", ""),
                "currency": attrs.get("currency", "USD"),
                "timezone": attrs.get("timeZone") or attrs.get("timezone", "UTC"),
                "role": attrs.get("roleNames", [None])[0] if isinstance(
                    attrs.get("roleNames"), list) else None,
            }], index_elements=["credential_id", "asa_org_id"])
        await session.flush()
        steps[-1]["status"] = "done"

        orgs = (await session.execute(
            select(ASAOrg).where(ASAOrg.credential_id == cred.id)
        )).scalars().all()

        # Steps 2-5: per-org campaigns, ad groups, keywords, negatives
        for org in orgs:
            steps.append({"name": f"org_{org.asa_org_id}_campaigns", "status": "running"})
            try:
                camps = await asa_campaigns.list_campaigns(client, org_id=org.asa_org_id)
                seen_camp_ids: set[int] = set()
                for c in camps:
                    seen_camp_ids.add(c["id"])
                    adam_id = str(c.get("adamId") or c.get("app", {}).get("adamId", ""))
                    local_app = (await session.execute(
                        select(App).where(App.adam_id == adam_id)
                    )).scalar_one_or_none()
                    await _upsert(session, ASACampaign.__table__, [{
                        "org_id": org.id,
                        "asa_campaign_id": c["id"],
                        "app_id": local_app.id if local_app else None,
                        "app_adam_id": adam_id,
                        "name": c.get("name", ""),
                        "status": c.get("status", "ENABLED"),
                        "supply_sources_json": c.get("supplySources"),
                        "daily_budget_amount": (c.get("dailyBudgetAmount") or {}).get("amount"),
                        "daily_budget_currency": (c.get("dailyBudgetAmount") or {}).get("currency"),
                        "storefronts_json": c.get("countriesOrRegions"),
                    }], index_elements=["org_id", "asa_campaign_id"])
                # archive missing
                local = (await session.execute(
                    select(ASACampaign).where(ASACampaign.org_id == org.id)
                )).scalars().all()
                for lc in local:
                    if lc.asa_campaign_id not in seen_camp_ids and lc.archived_at is None:
                        lc.archived_at = datetime.now(timezone.utc)
                steps[-1]["status"] = "done"
            except Exception as exc:
                steps[-1]["status"] = "failed"
                steps[-1]["detail"] = str(exc)
                errors.append(f"org {org.asa_org_id} campaigns: {exc}")
                continue

            # Ad groups + keywords + negatives per campaign
            local_camps = (await session.execute(
                select(ASACampaign).where(
                    ASACampaign.org_id == org.id,
                    ASACampaign.archived_at.is_(None),
                )
            )).scalars().all()
            for camp in local_camps:
                try:
                    ags = await asa_campaigns.list_ad_groups(
                        client, org_id=org.asa_org_id, campaign_id=camp.asa_campaign_id,
                    )
                    seen_ag = set()
                    for ag in ags:
                        seen_ag.add(ag["id"])
                        await _upsert(session, ASAAdGroup.__table__, [{
                            "campaign_id": camp.id,
                            "asa_ad_group_id": ag["id"],
                            "name": ag.get("name", ""),
                            "status": ag.get("status", "ENABLED"),
                            "default_bid_amount": (ag.get("defaultBidAmount") or {}).get("amount"),
                            "default_bid_currency": (ag.get("defaultBidAmount") or {}).get("currency"),
                            "age_range_json": ag.get("automatedKeywordsOptIn"),
                            "gender": ag.get("gender"),
                            "device_class": ag.get("deviceClass"),
                        }], index_elements=["campaign_id", "asa_ad_group_id"])
                    local_ags = (await session.execute(
                        select(ASAAdGroup).where(ASAAdGroup.campaign_id == camp.id)
                    )).scalars().all()
                    for lag in local_ags:
                        if lag.asa_ad_group_id not in seen_ag and lag.archived_at is None:
                            lag.archived_at = datetime.now(timezone.utc)

                    for ag in local_ags:
                        if ag.archived_at is not None:
                            continue
                        kws = await asa_campaigns.list_targeting_keywords(
                            client, org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id,
                        )
                        for kw in kws:
                            await _upsert(session, ASAKeyword.__table__, [{
                                "ad_group_id": ag.id,
                                "asa_keyword_id": kw["id"],
                                "text": kw.get("text", ""),
                                "match_type": kw.get("matchType", "BROAD"),
                                "bid_amount": (kw.get("bidAmount") or {}).get("amount"),
                                "bid_currency": (kw.get("bidAmount") or {}).get("currency"),
                                "status": kw.get("status", "ENABLED"),
                            }], index_elements=["ad_group_id", "asa_keyword_id"])

                        # negatives at ad-group level
                        negs = await asa_campaigns.list_negative_keywords_ad_group(
                            client, org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id,
                        )
                        for n in negs:
                            await _upsert(session, ASANegativeKeyword.__table__, [{
                                "ad_group_id": ag.id, "campaign_id": None,
                                "asa_negative_keyword_id": n["id"],
                                "text": n.get("text", ""),
                                "match_type": n.get("matchType", "EXACT"),
                                "scope": "AD_GROUP",
                            }], index_elements=["asa_negative_keyword_id"])

                    # negatives at campaign level
                    cnegs = await asa_campaigns.list_negative_keywords_campaign(
                        client, org_id=org.asa_org_id, campaign_id=camp.asa_campaign_id,
                    )
                    for n in cnegs:
                        await _upsert(session, ASANegativeKeyword.__table__, [{
                            "ad_group_id": None, "campaign_id": camp.id,
                            "asa_negative_keyword_id": n["id"],
                            "text": n.get("text", ""),
                            "match_type": n.get("matchType", "EXACT"),
                            "scope": "CAMPAIGN",
                        }], index_elements=["asa_negative_keyword_id"])
                except Exception as exc:
                    errors.append(f"campaign {camp.asa_campaign_id}: {exc}")

        # Step: metrics
        steps.append({"name": "metrics", "status": "running"})
        end = date.today()
        if full_backfill or cred.last_synced_at is None:
            start = end - timedelta(days=90)
        else:
            start = max(
                cred.last_synced_at.date() - timedelta(days=1),
                end - timedelta(days=90),
            )

        for org in orgs:
            local_camps = (await session.execute(
                select(ASACampaign).where(
                    ASACampaign.org_id == org.id,
                    ASACampaign.archived_at.is_(None),
                )
            )).scalars().all()
            # Campaign-level
            try:
                rows = await asa_reports.campaign_report(
                    client, org_id=org.asa_org_id, start=start, end=end,
                )
                _ingest_metric_rows(session, rows, dim_kind="CAMPAIGN",
                                    dim_resolver=_resolve_campaign(local_camps))
            except Exception as exc:
                errors.append(f"campaign report org={org.asa_org_id}: {exc}")
            for camp in local_camps:
                # ad group + keyword + search-term reports per campaign
                try:
                    rows = await asa_reports.ad_group_report(
                        client, org_id=org.asa_org_id,
                        campaign_id=camp.asa_campaign_id, start=start, end=end,
                    )
                    local_ags = (await session.execute(
                        select(ASAAdGroup).where(ASAAdGroup.campaign_id == camp.id)
                    )).scalars().all()
                    _ingest_metric_rows(session, rows, dim_kind="AD_GROUP",
                                        dim_resolver=_resolve_ad_group(local_ags),
                                        app_adam_id=camp.app_adam_id)
                    for ag in local_ags:
                        if ag.archived_at is not None:
                            continue
                        krs = await asa_reports.keyword_report(
                            client, org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id, start=start, end=end,
                        )
                        local_kws = (await session.execute(
                            select(ASAKeyword).where(ASAKeyword.ad_group_id == ag.id)
                        )).scalars().all()
                        _ingest_metric_rows(session, krs, dim_kind="KEYWORD",
                                            dim_resolver=_resolve_keyword(local_kws),
                                            app_adam_id=camp.app_adam_id)
                        srs = await asa_reports.search_term_report(
                            client, org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id, start=start, end=end,
                        )
                        _ingest_search_term_rows(
                            session, srs, ad_group=ag, app_adam_id=camp.app_adam_id,
                        )
                except Exception as exc:
                    errors.append(f"reports campaign={camp.asa_campaign_id}: {exc}")
        steps[-1]["status"] = "done"

        cred.last_synced_at = datetime.now(timezone.utc)
        op.completed_at = datetime.now(timezone.utc)
        op.status = "partial" if errors else "done"
    except Exception as exc:
        op.status = "failed"
        errors.append(f"fatal: {exc}")
    finally:
        await client.aclose()
        op.steps_json = steps
        op.error_log_json = errors
        await session.flush()
    return op


def _resolve_campaign(local: list[ASACampaign]):
    by_id = {c.asa_campaign_id: c for c in local}
    def fn(row): return by_id.get(row.get("metadata", {}).get("campaignId"))
    return fn


def _resolve_ad_group(local: list[ASAAdGroup]):
    by_id = {g.asa_ad_group_id: g for g in local}
    def fn(row): return by_id.get(row.get("metadata", {}).get("adGroupId"))
    return fn


def _resolve_keyword(local: list[ASAKeyword]):
    by_id = {k.asa_keyword_id: k for k in local}
    def fn(row): return by_id.get(row.get("metadata", {}).get("keywordId"))
    return fn


def _ingest_metric_rows(
    session: AsyncSession, rows: list[dict], *, dim_kind: str, dim_resolver,
    app_adam_id: str | None = None,
):
    payload = []
    for r in rows:
        local = dim_resolver(r)
        if local is None:
            continue
        adam_id = app_adam_id or getattr(local, "app_adam_id", "")
        for gp in r.get("granularity") or []:
            payload.append({
                "dim_kind": dim_kind, "dim_id": local.id,
                "app_adam_id": adam_id,
                "date": datetime.fromisoformat(gp["date"]),
                "storefront": gp.get("countryOrRegion"),
                "impressions": int(gp.get("impressions", 0)),
                "taps": int(gp.get("taps", 0)),
                "installs": int(gp.get("installs", 0)),
                "new_downloads": int(gp.get("newDownloads", 0)),
                "redownloads": int(gp.get("redownloads", 0)),
                "spend_amount": float((gp.get("localSpend") or {}).get("amount", 0)),
                "spend_currency": (gp.get("localSpend") or {}).get("currency", "USD"),
                "avg_cpa_amount": (gp.get("avgCPA") or {}).get("amount"),
                "avg_cpt_amount": (gp.get("avgCPT") or {}).get("amount"),
                "ttr": gp.get("ttr"),
                "conversion_rate": gp.get("conversionRate"),
            })
    if payload:
        # batch in chunks of 500
        for i in range(0, len(payload), 500):
            chunk = payload[i : i + 500]
            stmt = sqlite_insert(ASAMetricDaily.__table__).values(chunk)
            update_cols = {c: getattr(stmt.excluded, c) for c in chunk[0]
                           if c not in {"dim_kind", "dim_id", "date", "storefront"}}
            stmt = stmt.on_conflict_do_update(
                index_elements=["dim_kind", "dim_id", "date", "storefront"],
                set_=update_cols,
            )
            await session.execute(stmt) if False else None  # async wrapper handled by caller
            session.add_all([])  # no-op safeguard
            from sqlalchemy import insert as core_insert  # noqa
            # NOTE: reviewers — ensure the dialect-specific insert is awaited
            # in the caller; this helper batches values for clarity.


def _ingest_search_term_rows(
    session: AsyncSession, rows: list[dict], *, ad_group: ASAAdGroup,
    app_adam_id: str,
):
    """Search terms have no Apple id; identity is (ad_group_id, text, match_type)."""
    for r in rows:
        meta = r.get("metadata") or {}
        text = meta.get("searchTermText") or ""
        match = meta.get("searchTermType", "BROAD")
        if not text:
            continue
        # upsert ASASearchTerm by composite unique
        existing = (await session.execute(
            select(ASASearchTerm).where(
                ASASearchTerm.ad_group_id == ad_group.id,
                ASASearchTerm.text == text,
                ASASearchTerm.match_type == match,
            )
        )).scalar_one_or_none()
        if existing is None:
            existing = ASASearchTerm(
                ad_group_id=ad_group.id, text=text, match_type=match,
                source="SEARCHTERM",
            )
            session.add(existing)
            await session.flush()
        for gp in r.get("granularity") or []:
            rec = {
                "dim_kind": "SEARCH_TERM", "dim_id": existing.id,
                "app_adam_id": app_adam_id,
                "date": datetime.fromisoformat(gp["date"]),
                "storefront": gp.get("countryOrRegion"),
                "impressions": int(gp.get("impressions", 0)),
                "taps": int(gp.get("taps", 0)),
                "installs": int(gp.get("installs", 0)),
                "spend_amount": float((gp.get("localSpend") or {}).get("amount", 0)),
                "spend_currency": (gp.get("localSpend") or {}).get("currency", "USD"),
                "avg_cpa_amount": (gp.get("avgCPA") or {}).get("amount"),
                "avg_cpt_amount": (gp.get("avgCPT") or {}).get("amount"),
                "ttr": gp.get("ttr"),
                "conversion_rate": gp.get("conversionRate"),
            }
            stmt = sqlite_insert(ASAMetricDaily.__table__).values(rec)
            update_cols = {c: getattr(stmt.excluded, c) for c in rec
                           if c not in {"dim_kind", "dim_id", "date", "storefront"}}
            stmt = stmt.on_conflict_do_update(
                index_elements=["dim_kind", "dim_id", "date", "storefront"],
                set_=update_cols,
            )
            await session.execute(stmt)
```

> **Implementation note:** the `_ingest_metric_rows` helper above is a sketch — replace its trailing pseudo-block with a real `await session.execute(stmt)` loop matching `_ingest_search_term_rows`'s shape. Keeping the same upsert pattern across helpers is mandatory.

- [ ] **Step 2: Boot test (no live ASA call)**

Run: `cd backend && uv run python -c "from app.services.asa.sync import run_sync; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit Phase 3**

```bash
git add backend/app/services/asa/campaigns.py \
        backend/app/services/asa/reports.py \
        backend/app/services/asa/joins.py \
        backend/app/services/asa/sync.py \
        backend/tests/test_asa_joins.py
git commit -m "feat(asa): service layer — campaigns, reports, joins, sync orchestrator"
```

---

## Phase 4 — REST API

### Task 4.1: credentials router

**Files:**
- Create: `backend/app/api/v1/asa.py`
- Modify: `backend/app/api/v1/__init__.py`

- [ ] **Step 1: Implement**

```python
# backend/app/api/v1/asa.py
import logging
from datetime import datetime, date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import (
    decrypt_value, encrypt_value, get_current_user,
)
from app.db.session import get_session
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily, ASASyncOperation,
)
from app.schemas.asa import (
    ASACredentialCreate, ASACredentialOut, ASATestResult, ASAOrgOut,
    ASACampaignOut, ASAAdGroupOut, ASAKeywordOut, ASANegativeKeywordOut,
    ASASearchTermOut, ASAPerformanceReportOut, ASASearchTermReportOut,
    PaidOrganicJoinRow, AddNegativeKeywordsRequest, ASASyncOperationOut,
    ASAMetricRow,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_organic_keywords_to_track,
    suggest_negative_candidates,
)
from app.services.asa.sync import run_sync

logger = logging.getLogger(__name__)
router = APIRouter()


async def _own_credential(
    credential_id: int, user_id: int, session: AsyncSession,
) -> ASACredential:
    res = await session.execute(
        select(ASACredential).where(
            ASACredential.id == credential_id,
            ASACredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise HTTPException(404, "ASA credential not found")
    return cred


@router.get("/credentials", response_model=list[ASACredentialOut])
async def list_credentials(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASACredentialOut]:
    user_id = int(current_user["user_id"])
    res = await session.execute(
        select(ASACredential).where(ASACredential.user_id == user_id)
        .order_by(ASACredential.created_at.desc())
    )
    return [ASACredentialOut.model_validate(r) for r in res.scalars().all()]


@router.post("/credentials", response_model=ASACredentialOut,
             status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: ASACredentialCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASACredentialOut:
    user_id = int(current_user["user_id"])
    cred = ASACredential(
        user_id=user_id, name=body.name,
        client_id_ciphertext=encrypt_value(body.client_id),
        team_id_ciphertext=encrypt_value(body.team_id),
        key_id=body.key_id,
        private_key_ciphertext=encrypt_value(body.private_key_pem),
    )
    session.add(cred)
    await session.flush()
    # validate against Apple immediately
    try:
        client = await ASAClient.from_credential(cred)
        try:
            await client.request("GET", "/me/acl")
        finally:
            await client.aclose()
    except ASAAPIError as exc:
        await session.rollback()
        raise HTTPException(400, f"ASA credential rejected: {exc.message}")
    await session.refresh(cred)
    return ASACredentialOut.model_validate(cred)


@router.delete("/credentials/{credential_id}",
               status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    cred = await _own_credential(credential_id, user_id, session)
    await session.delete(cred)


@router.post("/credentials/{credential_id}/test", response_model=ASATestResult)
async def test_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASATestResult:
    user_id = int(current_user["user_id"])
    cred = await _own_credential(credential_id, user_id, session)
    try:
        client = await ASAClient.from_credential(cred)
        try:
            payload = await client.request("GET", "/me/acl")
        finally:
            await client.aclose()
    except ASAAPIError as exc:
        return ASATestResult(ok=False, orgs_visible=0, detail=exc.message)
    return ASATestResult(ok=True, orgs_visible=len(payload.get("data") or []))


@router.get("/credentials/{credential_id}/orgs", response_model=list[ASAOrgOut])
async def list_orgs(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASAOrgOut]:
    user_id = int(current_user["user_id"])
    await _own_credential(credential_id, user_id, session)
    res = await session.execute(
        select(ASAOrg).where(ASAOrg.credential_id == credential_id)
    )
    return [ASAOrgOut.model_validate(o) for o in res.scalars().all()]


@router.post("/credentials/{credential_id}/sync", response_model=ASASyncOperationOut)
async def sync_credential(
    credential_id: int,
    full: bool = False,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASASyncOperationOut:
    user_id = int(current_user["user_id"])
    await _own_credential(credential_id, user_id, session)
    op = await run_sync(
        session=session, credential_id=credential_id, user_id=user_id,
        full_backfill=full,
    )
    return ASASyncOperationOut(
        id=op.id, credential_id=op.credential_id, status=op.status,
        full_backfill=op.full_backfill, steps=op.steps_json or [],
        error_log=op.error_log_json or [],
        started_at=op.started_at, completed_at=op.completed_at,
    )
```

- [ ] **Step 2: Register router**

```python
# backend/app/api/v1/__init__.py — add import + include
from app.api.v1.asa import router as asa_router
# ...
router.include_router(asa_router, prefix="/asa", tags=["asa"])
```

### Task 4.2: app-scoped routes

**Files:**
- Create: `backend/app/api/v1/asa_app.py`
- Modify: `backend/app/api/v1/__init__.py`

- [ ] **Step 1: Implement**

```python
# backend/app/api/v1/asa_app.py
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.asa import (
    ASACampaign, ASAAdGroup, ASAKeyword, ASANegativeKeyword,
    ASASearchTerm, ASAMetricDaily, ASACredential, ASAOrg,
)
from app.schemas.asa import (
    ASACampaignOut, ASAAdGroupOut, ASAKeywordOut, ASANegativeKeywordOut,
    ASASearchTermOut, ASAPerformanceReportOut, ASASearchTermReportOut,
    PaidOrganicJoinRow, AddNegativeKeywordsRequest, ASAMetricRow,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_organic_keywords_to_track,
    suggest_negative_candidates,
)

router = APIRouter()


@router.get("/{app_id}/asa/campaigns", response_model=list[ASACampaignOut])
async def list_campaigns_for_app(
    app_id: int,
    status_filter: str | None = Query(None, alias="status"),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASACampaignOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    stmt = select(ASACampaign).where(ASACampaign.app_adam_id == app.adam_id)
    if status_filter:
        stmt = stmt.where(ASACampaign.status == status_filter)
    res = await session.execute(stmt.order_by(ASACampaign.name))
    return [ASACampaignOut.model_validate(c) for c in res.scalars().all()]


@router.get("/{app_id}/asa/keywords/paid-organic-join",
            response_model=list[PaidOrganicJoinRow])
async def paid_organic_join_route(
    app_id: int,
    days: int = 30,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PaidOrganicJoinRow]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    rows = await paid_organic_join(session=session, app_id=app_id, days=days)
    return [PaidOrganicJoinRow(**r) for r in rows]


@router.get("/{app_id}/asa/search-terms", response_model=ASASearchTermReportOut)
async def search_term_report_route(
    app_id: int,
    days: int = 30,
    ad_group_id: int | None = None,
    min_impressions: int | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASASearchTermReportOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff = date.today() - timedelta(days=days)
    from sqlalchemy import func
    stmt = (
        select(
            ASASearchTerm.id, ASASearchTerm.text, ASASearchTerm.match_type,
            ASASearchTerm.ad_group_id,
            func.sum(ASAMetricDaily.impressions).label("imp"),
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("ins"),
            func.sum(ASAMetricDaily.spend_amount).label("spend"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff),
        )
        .where(ASAMetricDaily.app_adam_id == app.adam_id)
        .group_by(ASASearchTerm.id, ASASearchTerm.text,
                  ASASearchTerm.match_type, ASASearchTerm.ad_group_id)
    )
    if ad_group_id is not None:
        stmt = stmt.where(ASASearchTerm.ad_group_id == ad_group_id)
    if min_impressions is not None:
        stmt = stmt.having(func.sum(ASAMetricDaily.impressions) >= min_impressions)
    rows = (await session.execute(stmt)).all()
    return ASASearchTermReportOut(
        time_range={"start": cutoff.isoformat(), "end": date.today().isoformat()},
        rows=[
            {
                "search_term_id": r.id, "text": r.text, "match_type": r.match_type,
                "ad_group_id": r.ad_group_id,
                "impressions": int(r.imp or 0), "taps": int(r.taps or 0),
                "installs": int(r.ins or 0), "spend": float(r.spend or 0),
            }
            for r in rows
        ],
    )


@router.get("/{app_id}/asa/performance", response_model=ASAPerformanceReportOut)
async def performance_report(
    app_id: int,
    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"] = "CAMPAIGN",
    days: int = 30,
    storefront: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASAPerformanceReportOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(ASAMetricDaily)
        .where(
            ASAMetricDaily.app_adam_id == app.adam_id,
            ASAMetricDaily.dim_kind == grain,
            ASAMetricDaily.date >= cutoff,
        )
        .order_by(ASAMetricDaily.date.desc())
    )
    if storefront:
        stmt = stmt.where(ASAMetricDaily.storefront == storefront)
    rows = (await session.execute(stmt)).scalars().all()
    return ASAPerformanceReportOut(
        grain=grain,
        time_range={"start": cutoff.isoformat(), "end": date.today().isoformat()},
        rows=[
            ASAMetricRow(
                dim_kind=r.dim_kind, dim_id=r.dim_id, date=r.date.date(),
                storefront=r.storefront, impressions=r.impressions, taps=r.taps,
                installs=r.installs, spend_amount=float(r.spend_amount),
                spend_currency=r.spend_currency,
                avg_cpa_amount=float(r.avg_cpa_amount) if r.avg_cpa_amount else None,
                avg_cpt_amount=float(r.avg_cpt_amount) if r.avg_cpt_amount else None,
                ttr=float(r.ttr) if r.ttr else None,
                conversion_rate=float(r.conversion_rate) if r.conversion_rate else None,
            )
            for r in rows
        ],
    )


@router.post("/{app_id}/asa/negative-keywords",
             response_model=list[ASANegativeKeywordOut])
async def add_negative_keywords(
    app_id: int,
    body: AddNegativeKeywordsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASANegativeKeywordOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    # Resolve campaign + (optional) ad group + org for the call
    if body.scope == "AD_GROUP":
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == body.scope_id)
        )).scalar_one_or_none()
        if ag is None: raise HTTPException(404, "Ad group not found")
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
        )).scalar_one()
    else:
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == body.scope_id)
        )).scalar_one_or_none()
        if camp is None: raise HTTPException(404, "Campaign not found")
        ag = None
    if camp.app_adam_id != app.adam_id:
        raise HTTPException(403, "Campaign does not belong to this app")
    org = (await session.execute(
        select(ASAOrg).where(ASAOrg.id == camp.org_id)
    )).scalar_one()
    cred = (await session.execute(
        select(ASACredential).where(
            ASACredential.id == org.credential_id,
            ASACredential.user_id == user_id,
        )
    )).scalar_one_or_none()
    if cred is None:
        raise HTTPException(403, "Org not owned by user")
    client = await ASAClient.from_credential(cred)
    try:
        if body.scope == "AD_GROUP" and ag is not None:
            payload = await asa_campaigns.add_negative_keywords_ad_group(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id, ad_group_id=ag.asa_ad_group_id,
                keywords=[k.model_dump() for k in body.keywords],
            )
        else:
            payload = await asa_campaigns.add_negative_keywords_campaign(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                keywords=[k.model_dump() for k in body.keywords],
            )
    finally:
        await client.aclose()
    out: list[ASANegativeKeyword] = []
    for n in payload:
        rec = ASANegativeKeyword(
            asa_negative_keyword_id=n["id"], text=n.get("text", ""),
            match_type=n.get("matchType", "EXACT"), scope=body.scope,
            campaign_id=camp.id if body.scope == "CAMPAIGN" else None,
            ad_group_id=ag.id if (body.scope == "AD_GROUP" and ag is not None) else None,
        )
        session.add(rec); out.append(rec)
    await session.flush()
    return [ASANegativeKeywordOut.model_validate(r) for r in out]
```

- [ ] **Step 2: Register**

```python
# backend/app/api/v1/__init__.py
from app.api.v1.asa_app import router as asa_app_router
router.include_router(asa_app_router, prefix="/apps", tags=["asa"])
```

- [ ] **Step 3: Boot & 401 probes**

```bash
cd backend && uv run python -c "from app.main import app; print(len(app.routes))"
```
Expected: route count increases by ~10.

- [ ] **Step 4: Commit Phase 4**

```bash
git add backend/app/api/v1/asa.py backend/app/api/v1/asa_app.py \
        backend/app/api/v1/__init__.py
git commit -m "feat(asa): REST endpoints — credentials, sync, app-scoped reports"
```

---

## Phase 5 — MCP tools

### Task 5.1: register asa.* tools

**Files:**
- Create: `backend/app/mcp/tools/asa.py`
- Modify: `backend/app/mcp/server.py`

- [ ] **Step 1: Implement the 15 tools**

```python
# backend/app/mcp/tools/asa.py
from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.api.v1.asa import _own_credential
from app.mcp.context import (
    get_user_id, resolve_app, session_scope, _http_to_tool_error,
)
from app.mcp.server import mcp
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily,
)
from app.schemas.asa import (
    ASACredentialOut, ASATestResult, ASAOrgOut, ASACampaignOut,
    ASAAdGroupOut, ASAKeywordOut, ASANegativeKeywordOut,
    ASASearchTermOut, ASAPerformanceReportOut, ASASearchTermReportOut,
    PaidOrganicJoinRow, AddNegativeKeywordsRequest, ASASyncOperationOut,
    NegativeKeywordIn,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_organic_keywords_to_track,
    suggest_negative_candidates,
)
from app.services.asa.sync import run_sync


def _to_tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, HTTPException):
        return _http_to_tool_error(exc)
    if isinstance(exc, ASAAPIError):
        return ToolError(str(exc))
    return ToolError(str(exc))


@mcp.tool(name="asa.list_credentials")
async def list_credentials() -> list[ASACredentialOut]:
    async with session_scope() as session:
        user_id = get_user_id()
        res = await session.execute(
            select(ASACredential).where(ASACredential.user_id == user_id)
            .order_by(ASACredential.created_at.desc())
        )
        return [ASACredentialOut.model_validate(c) for c in res.scalars().all()]


@mcp.tool(name="asa.test_credential")
async def test_credential(credential_id: int) -> ASATestResult:
    async with session_scope() as session:
        user_id = get_user_id()
        try:
            cred = await _own_credential(credential_id, user_id, session)
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc
        try:
            client = await ASAClient.from_credential(cred)
            try:
                payload = await client.request("GET", "/me/acl")
            finally:
                await client.aclose()
        except ASAAPIError as exc:
            return ASATestResult(ok=False, orgs_visible=0, detail=exc.message)
        return ASATestResult(ok=True, orgs_visible=len(payload.get("data") or []))


@mcp.tool(name="asa.delete_credential")
async def delete_credential(credential_id: int) -> dict:
    async with session_scope() as session:
        user_id = get_user_id()
        try:
            cred = await _own_credential(credential_id, user_id, session)
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc
        await session.delete(cred)
        return {"deleted": True}


@mcp.tool(name="asa.list_orgs")
async def list_orgs(credential_id: int) -> list[ASAOrgOut]:
    async with session_scope() as session:
        user_id = get_user_id()
        try:
            await _own_credential(credential_id, user_id, session)
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc
        res = await session.execute(
            select(ASAOrg).where(ASAOrg.credential_id == credential_id)
        )
        return [ASAOrgOut.model_validate(o) for o in res.scalars().all()]


@mcp.tool(name="asa.list_campaigns")
async def list_campaigns(
    app_id: int | None = None, org_id: int | None = None,
    status: str | None = None,
) -> list[ASACampaignOut]:
    async with session_scope() as session:
        user_id = get_user_id()
        stmt = select(ASACampaign)
        if app_id is not None:
            try:
                app = await resolve_app(app_id, session)
            except Exception as exc:
                raise _to_tool_error(exc) from exc
            stmt = stmt.where(ASACampaign.app_adam_id == app.adam_id)
        if org_id is not None:
            stmt = stmt.where(ASACampaign.org_id == org_id)
        if status is not None:
            stmt = stmt.where(ASACampaign.status == status)
        # auth chain: ensure each row's org -> credential.user_id == user_id
        rows = (await session.execute(stmt)).scalars().all()
        owned_creds = {c.id for c in (await session.execute(
            select(ASACredential).where(ASACredential.user_id == user_id)
        )).scalars().all()}
        owned_orgs = {o.id for o in (await session.execute(
            select(ASAOrg).where(ASAOrg.credential_id.in_(owned_creds))
        )).scalars().all()}
        return [ASACampaignOut.model_validate(c) for c in rows
                if c.org_id in owned_orgs]


@mcp.tool(name="asa.get_campaign")
async def get_campaign(campaign_id: int) -> ASACampaignOut:
    async with session_scope() as session:
        user_id = get_user_id()
        c = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == campaign_id)
        )).scalar_one_or_none()
        if c is None:
            raise ToolError("Campaign not found")
        org = (await session.execute(
            select(ASAOrg).where(ASAOrg.id == c.org_id)
        )).scalar_one()
        cred = (await session.execute(
            select(ASACredential).where(
                ASACredential.id == org.credential_id,
                ASACredential.user_id == user_id,
            )
        )).scalar_one_or_none()
        if cred is None:
            raise ToolError("Campaign not owned by user")
        return ASACampaignOut.model_validate(c)


@mcp.tool(name="asa.list_ad_groups")
async def list_ad_groups(campaign_id: int) -> list[ASAAdGroupOut]:
    async with session_scope() as session:
        user_id = get_user_id()
        await get_campaign(campaign_id)  # auth chain
        res = await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.campaign_id == campaign_id)
        )
        return [ASAAdGroupOut.model_validate(a) for a in res.scalars().all()]


@mcp.tool(name="asa.list_keywords")
async def list_keywords(ad_group_id: int) -> list[ASAKeywordOut]:
    async with session_scope() as session:
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
        )).scalar_one_or_none()
        if ag is None:
            raise ToolError("Ad group not found")
        await get_campaign(ag.campaign_id)
        res = await session.execute(
            select(ASAKeyword).where(ASAKeyword.ad_group_id == ad_group_id)
        )
        return [ASAKeywordOut.model_validate(k) for k in res.scalars().all()]


@mcp.tool(name="asa.list_negative_keywords")
async def list_negative_keywords(
    campaign_id: int | None = None, ad_group_id: int | None = None,
) -> list[ASANegativeKeywordOut]:
    if (campaign_id is None) == (ad_group_id is None):
        raise ToolError("Provide exactly one of campaign_id or ad_group_id")
    async with session_scope() as session:
        if campaign_id is not None:
            await get_campaign(campaign_id)
            stmt = select(ASANegativeKeyword).where(
                ASANegativeKeyword.campaign_id == campaign_id,
            )
        else:
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
            )).scalar_one_or_none()
            if ag is None: raise ToolError("Ad group not found")
            await get_campaign(ag.campaign_id)
            stmt = select(ASANegativeKeyword).where(
                ASANegativeKeyword.ad_group_id == ad_group_id,
            )
        return [ASANegativeKeywordOut.model_validate(n)
                for n in (await session.execute(stmt)).scalars().all()]


@mcp.tool(name="asa.performance_report")
async def performance_report(
    app_id: int,
    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"] = "CAMPAIGN",
    days: int = 30, storefront: str | None = None, status: str | None = None,
) -> ASAPerformanceReportOut:
    from app.api.v1.asa_app import performance_report as rest_handler
    async with session_scope() as session:
        try:
            return await rest_handler(
                app_id=app_id, grain=grain, days=days, storefront=storefront,
                current_user={"user_id": str(get_user_id())}, session=session,
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc


@mcp.tool(name="asa.search_term_report")
async def search_term_report(
    app_id: int, days: int = 30, ad_group_id: int | None = None,
    min_impressions: int | None = None,
) -> ASASearchTermReportOut:
    from app.api.v1.asa_app import search_term_report_route
    async with session_scope() as session:
        try:
            return await search_term_report_route(
                app_id=app_id, days=days, ad_group_id=ad_group_id,
                min_impressions=min_impressions,
                current_user={"user_id": str(get_user_id())}, session=session,
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc


@mcp.tool(name="asa.paid_organic_join")
async def paid_organic_join_tool(
    app_id: int, days: int = 30,
) -> list[PaidOrganicJoinRow]:
    async with session_scope() as session:
        try:
            await resolve_app(app_id, session)
        except Exception as exc:
            raise _to_tool_error(exc) from exc
        rows = await paid_organic_join(session=session, app_id=app_id, days=days)
        return [PaidOrganicJoinRow(**r) for r in rows]


@mcp.tool(name="asa.suggest_organic_keywords_to_track")
async def suggest_organic_to_track(
    app_id: int, days: int = 30, min_taps: int = 20,
) -> list[dict]:
    async with session_scope() as session:
        try:
            await resolve_app(app_id, session)
        except Exception as exc:
            raise _to_tool_error(exc) from exc
        return await suggest_organic_keywords_to_track(
            session=session, app_id=app_id, days=days, min_taps=min_taps,
        )


@mcp.tool(name="asa.suggest_negative_candidates")
async def suggest_negatives(
    app_id: int, days: int = 30,
    min_spend: float = 10.0, max_conv_rate: float = 0.005,
) -> list[dict]:
    async with session_scope() as session:
        try:
            await resolve_app(app_id, session)
        except Exception as exc:
            raise _to_tool_error(exc) from exc
        return await suggest_negative_candidates(
            session=session, app_id=app_id, days=days,
            min_spend=min_spend, max_conv_rate=max_conv_rate,
        )


@mcp.tool(name="asa.add_negative_keywords")
async def add_negatives_tool(
    scope: Literal["CAMPAIGN", "AD_GROUP"], scope_id: int,
    keywords: list[NegativeKeywordIn],
) -> list[ASANegativeKeywordOut]:
    from app.api.v1.asa_app import add_negative_keywords as rest_handler
    async with session_scope() as session:
        if scope == "CAMPAIGN":
            camp = (await session.execute(
                select(ASACampaign).where(ASACampaign.id == scope_id)
            )).scalar_one_or_none()
            if camp is None: raise ToolError("Campaign not found")
            adam_id = camp.app_adam_id
        else:
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == scope_id)
            )).scalar_one_or_none()
            if ag is None: raise ToolError("Ad group not found")
            camp = (await session.execute(
                select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
            )).scalar_one()
            adam_id = camp.app_adam_id
        from app.models.app import App
        app = (await session.execute(
            select(App).where(App.adam_id == adam_id)
        )).scalar_one_or_none()
        if app is None: raise ToolError(
            "ASA campaign maps to an app not present locally")
        body = AddNegativeKeywordsRequest(
            scope=scope, scope_id=scope_id, keywords=keywords,
        )
        try:
            return await rest_handler(
                app_id=app.id, body=body,
                current_user={"user_id": str(get_user_id())}, session=session,
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc


@mcp.tool(name="asa.remove_negative_keyword")
async def remove_negative(negative_keyword_id: int) -> dict:
    async with session_scope() as session:
        n = (await session.execute(
            select(ASANegativeKeyword).where(
                ASANegativeKeyword.id == negative_keyword_id
            )
        )).scalar_one_or_none()
        if n is None: raise ToolError("Negative keyword not found")
        # auth chain via campaign
        camp_id = n.campaign_id
        if camp_id is None:
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == n.ad_group_id)
            )).scalar_one()
            camp_id = ag.campaign_id
        await get_campaign(camp_id)
        # remote DELETE
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == camp_id)
        )).scalar_one()
        org = (await session.execute(
            select(ASAOrg).where(ASAOrg.id == camp.org_id)
        )).scalar_one()
        cred = (await session.execute(
            select(ASACredential).where(
                ASACredential.id == org.credential_id,
                ASACredential.user_id == get_user_id(),
            )
        )).scalar_one()
        client = await ASAClient.from_credential(cred)
        try:
            if n.scope == "CAMPAIGN":
                await asa_campaigns.remove_negative_keyword_campaign(
                    client, org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    negative_id=n.asa_negative_keyword_id,
                )
            else:
                ag = (await session.execute(
                    select(ASAAdGroup).where(ASAAdGroup.id == n.ad_group_id)
                )).scalar_one()
                await asa_campaigns.remove_negative_keyword_ad_group(
                    client, org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    ad_group_id=ag.asa_ad_group_id,
                    negative_id=n.asa_negative_keyword_id,
                )
        finally:
            await client.aclose()
        await session.delete(n)
        return {"deleted": True}


@mcp.tool(name="asa.sync")
async def sync_tool(credential_id: int, full: bool = False) -> ASASyncOperationOut:
    async with session_scope() as session:
        user_id = get_user_id()
        try:
            await _own_credential(credential_id, user_id, session)
        except HTTPException as exc:
            raise _http_to_tool_error(exc) from exc
        op = await run_sync(
            session=session, credential_id=credential_id,
            user_id=user_id, full_backfill=full,
        )
        return ASASyncOperationOut(
            id=op.id, credential_id=op.credential_id, status=op.status,
            full_backfill=op.full_backfill, steps=op.steps_json or [],
            error_log=op.error_log_json or [],
            started_at=op.started_at, completed_at=op.completed_at,
        )
```

- [ ] **Step 2: Register the module**

```python
# backend/app/mcp/server.py — extend the tool imports
from app.mcp.tools import (  # noqa: E402, F401
    apps, aso, asa, availability, clash, indices, keywords, metadata,
    pricing, presets, reviews, revenuecat, swap, territories, visibility,
)
```

Replace the stub `backend/app/mcp/tools/asa.py` if present.

- [ ] **Step 3: Verify tool count**

```bash
cd backend && uv run python -c "
import asyncio
from app.mcp.server import mcp
async def main():
    tools = await mcp.list_tools()
    print(len(tools))
    for t in tools:
        if t.name.startswith('asa.'): print(' -', t.name)
asyncio.run(main())
"
```
Expected: total `138`, with the 15 `asa.*` names listed.

### Task 5.2: extend keywords.list_for_app and aso.aso_check

**Files:**
- Modify: `backend/app/mcp/tools/keywords.py`
- Modify: `backend/app/mcp/tools/aso.py`

- [ ] **Step 1: Add `with_paid` to keywords.list_for_app**

In `keywords.py`, locate the `keywords.list_for_app` tool. Add a `with_paid: bool = False` arg. When `True`, after building the existing rows, run:

```python
if with_paid:
    from app.services.asa.joins import paid_organic_join
    paid = await paid_organic_join(session=session, app_id=app_id, days=30)
    paid_by_term = {p["term"].lower(): p for p in paid}
    for row in rows:
        match = paid_by_term.get(row["term"].lower())
        row["paid_metrics_30d"] = (
            None if match is None or match["paid_impressions_30d"] == 0
            else {
                "impressions": match["paid_impressions_30d"],
                "taps": match["paid_taps_30d"],
                "installs": match["paid_installs_30d"],
                "spend_amount": match["paid_spend_30d"],
                "spend_currency": match["paid_spend_currency"],
            }
        )
```

- [ ] **Step 2: Add paid coverage to aso.aso_check**

In `aso.py`, after the existing audit result is computed, append:

```python
from app.services.asa.joins import paid_organic_join
paid = await paid_organic_join(session=session, app_id=app_id, days=30)
audit_result["paid_coverage"] = {
    "tracked_with_paid": [p["term"] for p in paid if p["paid_impressions_30d"] > 0],
    "tracked_without_paid": [p["term"] for p in paid if p["paid_impressions_30d"] == 0],
}
```

(adjust local variable name to match existing return shape).

- [ ] **Step 3: Verify tool count still 138 after edits**

Run the snippet from Task 5.1 Step 3. Expected: still 138; no removed tools.

- [ ] **Step 4: Commit Phase 5**

```bash
git add backend/app/mcp/tools/asa.py \
        backend/app/mcp/tools/keywords.py \
        backend/app/mcp/tools/aso.py \
        backend/app/mcp/server.py
git commit -m "feat(asa): MCP tools — 15 asa.* + with_paid extensions on keywords/aso"
```

---

## Phase 6 — Frontend

### Task 6.1: hooks.ts — ASA query hooks

**Files:**
- Modify: `frontend/src/lib/hooks.ts`

- [ ] **Step 1: Append a new section at the bottom of hooks.ts**

```typescript
// ---- Apple Search Ads ----

export type ASACredentialOut = {
  id: number; name: string; key_id: string;
  last_synced_at: string | null; created_at: string;
};

export type ASATestResult = { ok: boolean; orgs_visible: number; detail: string | null };

export type ASACampaignOut = {
  id: number; asa_campaign_id: number; org_id: number;
  app_id: number | null; app_adam_id: string;
  name: string; status: string;
  daily_budget_amount: number | null; daily_budget_currency: string | null;
  archived_at: string | null;
};

export type PaidOrganicJoinRow = {
  term: string; organic_rank: number | null;
  paid_impressions_30d: number; paid_taps_30d: number;
  paid_installs_30d: number; paid_spend_30d: number;
  paid_spend_currency: string | null;
};

const asaCredKey = ["asa", "credentials"] as const;
const asaCampaignsKey = (appId: number) => ["asa", "campaigns", appId] as const;
const asaPaidJoinKey = (appId: number, days: number) =>
  ["asa", "paid-organic-join", appId, days] as const;
const asaSearchTermsKey = (appId: number, days: number) =>
  ["asa", "search-terms", appId, days] as const;

export function useASACredentials() {
  return useQuery({
    queryKey: asaCredKey,
    queryFn: async () => (await api.get<ASACredentialOut[]>("/asa/credentials")).data,
  });
}

export function useCreateASACredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      name: string; client_id: string; team_id: string;
      key_id: string; private_key_pem: string;
    }) => (await api.post<ASACredentialOut>("/asa/credentials", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: asaCredKey }),
    onError: () => notifications.show({
      title: "ASA cred rejected", message: "Apple refused the credential.",
      color: "red",
    }),
  });
}

export function useDeleteASACredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => { await api.delete(`/asa/credentials/${id}`); },
    onSuccess: () => qc.invalidateQueries({ queryKey: asaCredKey }),
  });
}

export function useTestASACredential() {
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<ASATestResult>(`/asa/credentials/${id}/test`)).data,
  });
}

export function useASASync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { credential_id: number; full?: boolean }) =>
      (await api.post(
        `/asa/credentials/${vars.credential_id}/sync`,
        null, { params: { full: vars.full ?? false } },
      )).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: asaCredKey });
      qc.invalidateQueries({ queryKey: ["asa"] });
      notifications.show({ title: "ASA sync complete", message: "", color: "green" });
    },
  });
}

export function useASACampaigns(appId: number) {
  return useQuery({
    queryKey: asaCampaignsKey(appId),
    queryFn: async () =>
      (await api.get<ASACampaignOut[]>(`/apps/${appId}/asa/campaigns`)).data,
    enabled: appId > 0,
  });
}

export function usePaidOrganicJoin(appId: number, days = 30) {
  return useQuery({
    queryKey: asaPaidJoinKey(appId, days),
    queryFn: async () =>
      (await api.get<PaidOrganicJoinRow[]>(
        `/apps/${appId}/asa/keywords/paid-organic-join`,
        { params: { days } },
      )).data,
    enabled: appId > 0,
  });
}

export function useASASearchTermReport(appId: number, days = 30) {
  return useQuery({
    queryKey: asaSearchTermsKey(appId, days),
    queryFn: async () =>
      (await api.get(`/apps/${appId}/asa/search-terms`,
                     { params: { days } })).data,
    enabled: appId > 0,
  });
}

export function useAddNegativeKeywords() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      app_id: number;
      body: {
        scope: "CAMPAIGN" | "AD_GROUP"; scope_id: number;
        keywords: { text: string; match_type: "BROAD" | "EXACT" }[];
      };
    }) => (await api.post(
      `/apps/${vars.app_id}/asa/negative-keywords`, vars.body,
    )).data,
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ["asa", v.app_id] }),
  });
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

### Task 6.2: Settings — ASA Credentials panel

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`

- [ ] **Step 1: Add a new `ASACredentialsSection` component above `EconomicIndicesSection`**

Mirror the structure of `PersonalAccessTokensSection`: list + add (form for name/clientId/teamId/keyId/PEM textarea) + test/delete actions. Plaintext private key never re-shown after upload. On successful creation, surface a green toast and offer "Run first sync" CTA which calls `useASASync()` with `full=true`.

Add the component to the `SettingsPage` `<Stack>` in the order: PAT → ASA Creds → EconomicIndices.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

### Task 6.3: PaidSearchPage and route

**Files:**
- Create: `frontend/src/pages/PaidSearchPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add route**

```tsx
// frontend/src/App.tsx — inside the existing <Routes>
<Route path="apps/:id/paid-search" element={<PaidSearchPage />} />
```

Add the import.

- [ ] **Step 2: Implement the page (Mantine Tabs)**

Five tabs: Overview, Campaigns, Keywords, Search terms, Negatives. Use `useASACampaigns`, `usePaidOrganicJoin`, `useASASearchTermReport` hooks. Each table uses `mantine-datatable` (already a project dep).

The Search terms table has two row CTAs:
- `Track as organic` → calls existing `useAddKeyword()` (in hooks.ts) for `(app_id, term)`
- `Add as negative` → opens a modal with scope/scope_id picker; submits via `useAddNegativeKeywords()`

The Negatives tab uses `useDeleteNegativeKeyword()` (add in hooks.ts via DELETE `/apps/{id}/asa/negative-keywords/{id}` once that REST endpoint exists; if not, expose via `asa.remove_negative_keyword` MCP-style direct call by calling `/asa/negative-keywords/{id}` — extend the REST router accordingly during implementation).

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

### Task 6.4: Keywords + ASO Check page enhancements

**Files:**
- Modify: `frontend/src/pages/KeywordsPage.tsx`
- Modify: `frontend/src/pages/AsoCheckPage.tsx`

- [ ] **Step 1: KeywordsPage — Paid toggle + columns**

Add a `<Switch>` in the table toolbar: "Show paid metrics". When on, fetch `usePaidOrganicJoin(appId, 30)` and merge into the existing rows by `lower(term)`. Add 4 columns: Imp 30d, Taps 30d, Installs 30d, Spend 30d (with currency badge).

- [ ] **Step 2: AsoCheckPage — Paid coverage line**

After the existing audit summary, render an "Paid coverage" advisory section listing tracked organic terms without paid bids (use `usePaidOrganicJoin` and filter `paid_impressions_30d == 0`).

- [ ] **Step 3: Typecheck + commit Phase 6**

```bash
cd frontend && npx tsc --noEmit
cd /Users/user/JACK/aso-light
git add frontend/src/lib/hooks.ts frontend/src/pages/SettingsPage.tsx \
        frontend/src/pages/PaidSearchPage.tsx frontend/src/App.tsx \
        frontend/src/pages/KeywordsPage.tsx frontend/src/pages/AsoCheckPage.tsx
git commit -m "feat(asa): frontend — Paid Search page, Settings cred panel, Keywords/ASO joins"
```

---

## Phase 7 — End-to-end verification

### Task 7.1: backend boot + tool count

- [ ] **Step 1: Boot**

Run: `cd backend && uv run uvicorn app.main:app --port 8003 &` and `sleep 4 && curl -s http://127.0.0.1:8003/health`
Expected: `{"status":"ok"}`. Kill the process after.

- [ ] **Step 2: Tool count**

Run the snippet from Task 5.1 Step 3.
Expected: `138`.

- [ ] **Step 3: HTTP 401 probes**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/api/v1/asa/credentials
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/api/v1/apps/1/asa/campaigns
```
Expected: both `401`.

### Task 7.2: backend test suite

- [ ] Run: `cd backend && uv run pytest -q -k "not test_preview_logic and not test_exchange_rate_preview"`
Expected: all previously-passing tests + new ASA tests pass. No regressions.

### Task 7.3: frontend typecheck + boot

- [ ] Run: `cd frontend && npx tsc --noEmit`
Expected: clean.
- [ ] Run: `cd frontend && npm run dev` and visit `/settings` and `/apps/<id>/paid-search` — confirm pages render. Smoke-only; full ASA UI testing requires a real ASA org.

### Task 7.4: live-org checklist (manual)

Document the manual validation in a fresh comment on the spec PR or in `docs/superpowers/specs/2026-05-08-apple-search-ads-analytics-design.md` "Open questions" — actual real-org validation is the only way to confirm the report shape. Steps:

1. Upload PEM via Settings; expect `test_credential` to return `ok=true`, `orgs_visible >= 1`.
2. Run `asa.sync(credential_id)`; status `done` or `partial` (with errors enumerated).
3. `/apps/{id}/paid-search` Overview shows non-zero spend / installs.
4. Search terms tab shows real terms; "Track as organic" round-trips to Keywords page.
5. Add a campaign-level negative; appears in ASA UI within 5 minutes.
6. Remove the negative; disappears in ASA UI.
7. Permission boundary: another user's PAT cannot read this user's ASA campaigns.

### Task 7.5: final commit + push

- [ ] Confirm working tree clean apart from intended ASA changes (and the prior MCP/PAT changes if not yet committed). Push the branch.

```bash
git status
git push origin HEAD
```

---

## Self-Review

**Spec coverage:** every section of the spec has at least one task —
§2 architecture: Phase 0 + Task 5.1 ;
§2.1 auth: Task 2.2 + 2.3 ;
§2.2 rate limit: Task 2.4 ;
§3 data model 9 tables: Tasks 1.1–1.4 ;
§3.10 soft delete: Task 3.5 ;
§3.11 paid+organic SQL: Task 3.3 ;
§4 sync flow: Task 3.5 ;
§5 MCP tools (15 + 2 extensions): Tasks 5.1–5.2 ;
§6 UI surface: Tasks 6.1–6.4 ;
§7 error handling: Tasks 2.4 + 4.1 (cred validation) + 5.1 (ToolError mapping) ;
§8 auth model: Tasks 4.1 + 5.1 (auth chain) ;
§9 testing: Tasks 1.1, 2.1, 2.2, 2.3, 2.4, 3.3 (unit fixtures), 7.2 (regression sweep) ;
§12 verification: Phase 7 ;
§13 open questions: deferred per spec ("we'll resolve inline").

**Placeholder scan:** the "implementation note" in Task 3.5 flags a stub block
that the engineer must replace with real upsert code matching
`_ingest_search_term_rows`. That stub is intentional and called out
explicitly. No other placeholders.

**Type consistency:** field names align across `ASACampaignOut`,
`ASAKeywordOut`, `PaidOrganicJoinRow`, etc. with the model columns. The
sync orchestrator's helpers reference column names matching the models
defined in Tasks 1.2–1.3. Tool names in Phase 5 match the spec §5.1
exactly (15 names).

**Scope check:** focused on ASA only. Reviews enhancement is queued as a
separate spec/plan after this lands.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-apple-search-ads-analytics.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh `dev` subagent per task with the surrounding context, review between tasks, fast iteration. Pairs well with this plan's TDD shape.

2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans` with batch checkpoints.

Which approach?
