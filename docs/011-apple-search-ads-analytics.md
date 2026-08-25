# 011 - Apple Search Ads Analytics

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md), [003 - Keyword Analysis](003-keyword-analysis.md)
**Related**: [007 - MCP Integration](007-mcp-integration.md), [010 - Keyword Intelligence](010-keyword-intelligence.md), [012 - Growth Recommendations](012-growth-recommendations.md)

## Overview

Full Apple Search Ads (ASA) analytics pipeline: ingest campaign/ad-group/keyword/search-term data from the ASA Reporting API into a local DB, compute daily metric rolls, and surface them in the Paid Search page and via MCP tools.

The credential hierarchy mirrors Apple's own: one **ASACredential** (per user, per teamId) may span many **ASAOrgs** (campaigns in different storefronts), each of which contains campaigns → ad groups → keywords/search-terms. All secrets are Fernet-encrypted at rest.

## Authentication Flow

Apple Search Ads uses a two-step OAuth2 flow with ES256-signed JWTs:

```
1. Build client_assertion JWT (ES256, sub=clientId, iss=teamId, aud=ASA token URL)
2. POST /oauth2/token → access_token (TTL 1h), cached per-process
   Header X-AP-Context: orgId=<orgId> selects storefront
3. Bearer {access_token} on all API calls
4. 401 → auto-retry with fresh token (one retry, then raise)
5. 429 → exponential backoff (up to 5 retries)
```

Key files:
- **`backend/app/services/asa/auth.py`** — `get_access_token(cred)`, per-process LRU cache, client_secret TTL 2h
- **`backend/app/services/asa/client.py`** — `ASAClient(base_url, access_token, org_id)`, retry/backoff middleware

## DB Models

**File**: `backend/app/models/asa.py`

```
ASACredential  (per user)
 └─ ASAOrg     (per credential × Apple org)
     └─ ASACampaign  (linked to local App via app_adam_id)
         └─ ASAAdGroup
             ├─ ASAKeyword
             ├─ ASANegativeKeyword
             └─ ASASearchTerm

ASAMetricDaily   (polymorphic fact table)
  dim_kind: "campaign" | "ad_group" | "keyword" | "search_term"
  dim_id:   FK to the relevant dimension table
  app_adam_id:  denormalised for fast app-scoped rollups
  date, impressions, taps, installs, spend (Decimal), ttr, cpa, cpt
  Index: (app_adam_id, dim_kind, date) for time-series queries
```

`ASASyncOperation` tracks the last sync per credential: `started_at`, `finished_at`, `status`, `error`.

## Sync Orchestrator

**File**: `backend/app/services/asa/sync.py` — `run_sync(credential_id, session, *, full_backfill=False)`

1. Fetch orgs via `GET /v5/users/me/orgs` and upsert into `ASAOrg`.
2. For each enabled org, fetch campaigns (`GET /v5/campaigns`) and their ad groups / keywords / search-terms.
3. Pull daily campaign-level reports (`GET /v5/reports/campaigns`) from `max(last_synced_at, today-90d)`.
4. Pull ad-group, keyword, and search-term granularity reports similarly.
5. Upsert all rows via SQLAlchemy `on_conflict_do_update` (Postgres) / `replace` (SQLite).
6. Mark `ASASyncOperation.status = "success"`.

`full_backfill=True` sets start date to `2020-01-01`.

## REST API

**Credential-scoped** (`backend/app/api/v1/asa.py`, prefix `/asa`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/asa/credentials` | List user's ASA credentials |
| POST | `/asa/credentials` | Add new credential (client_id, team_id, key_id, .p8 upload) |
| DELETE | `/asa/credentials/{id}` | Delete credential |
| POST | `/asa/credentials/{id}/test` | Validate credential (OAuth2 round-trip) |
| GET | `/asa/credentials/{id}/orgs` | List orgs for credential |
| POST | `/asa/sync` | Trigger sync for a credential |
| GET | `/asa/sync/{operation_id}` | Poll sync status |

**Per-app** (`backend/app/api/v1/asa_app.py`, prefix `/apps`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/{app_id}/asa/campaigns` | Campaigns linked to this app |
| GET | `/{app_id}/asa/campaigns/{id}` | Single campaign |
| GET | `/{app_id}/asa/campaigns/{id}/ad-groups` | Ad groups |
| GET | `/{app_id}/asa/ad-groups/{id}/keywords` | Bidded keywords |
| GET | `/{app_id}/asa/ad-groups/{id}/negative-keywords` | Negative keywords |
| POST | `/{app_id}/asa/ad-groups/{id}/negative-keywords` | Add negatives (batch) |
| DELETE | `/{app_id}/asa/negative-keywords/{id}` | Remove one negative |
| GET | `/{app_id}/asa/performance` | Campaign/ad-group performance report `?days=30&granularity=CAMPAIGN` |
| GET | `/{app_id}/asa/search-terms` | Search-term report `?days=30&ad_group_id=` |
| GET | `/{app_id}/asa/paid-organic-join` | Paid+organic join `?days=30` |
| GET | `/{app_id}/asa/suggest-negatives` | Negative keyword candidates `?days=30&min_spend=10` |
| GET | `/{app_id}/asa/suggest-organic-track` | Paid winners to add to organic tracker |

## Paid-Organic Join

**File**: `backend/app/services/asa/joins.py`

`paid_organic_join(session, app_id, days)` — for every search term that has ASA impressions in the window, looks up the organic rank from `keyword_rankings` (latest snapshot). Returns `PaidOrganicJoinRow(text, taps, installs, spend, organic_rank)`.

`suggest_organic_keywords_to_track` — filters join rows where `taps >= min_taps` and keyword is not already tracked organically.

`suggest_negative_candidates` — filters rows where `spend >= min_spend` and `installs / taps <= max_conv_rate`.

## MCP Tools

17 tools registered under the `asa.*` namespace. **File**: `backend/app/mcp/tools/asa.py`.

| Tool | Description |
|------|-------------|
| `asa.list_credentials` | List all ASA credentials for the PAT owner |
| `asa.test_credential` | Validate a credential's OAuth2 round-trip |
| `asa.delete_credential` | Remove a credential |
| `asa.list_orgs` | Orgs for a credential |
| `asa.list_campaigns` | Campaigns linked to an app |
| `asa.get_campaign` | Single campaign details |
| `asa.list_ad_groups` | Ad groups for a campaign |
| `asa.list_keywords` | Bidded keywords for an ad group |
| `asa.list_negative_keywords` | Negatives for a campaign or ad group |
| `asa.performance_report` | KPI metrics `(spend, installs, CPI, CPT, CTR, CR)` at campaign or ad-group granularity |
| `asa.search_term_report` | Per-search-term taps/installs/spend `?ad_group_id` |
| `asa.paid_organic_join` | Bridge paid search-terms with organic ranks |
| `asa.suggest_organic_keywords_to_track` | Paid winners not yet tracked organically |
| `asa.suggest_negative_candidates` | High-spend, low-conversion search terms |
| `asa.add_negative_keywords` | Batch-add negatives to a campaign or ad group |
| `asa.remove_negative_keyword` | Delete one negative keyword |
| `asa.sync` | Trigger a credential sync |

## Frontend — Paid Search Page

**File**: `frontend/src/pages/PaidSearchPage.tsx`

Five tabs:

| Tab | Content |
|-----|---------|
| **Overview** | Period selector (7d/30d/90d), 6 KPI tiles with prior-period delta (Spend/Installs/CPI/CPT/CTR/Conversion), LineCharts for Spend and Installs trend |
| **Campaigns** | DataTable of campaigns with spend/installs/CPI; click row → drilldown drawer |
| **Keywords** | Bidded keywords with match type, bid, taps, installs, spend |
| **Search Terms** | Raw search-term report — the actual queries triggering impressions |
| **Negatives** | Negative keyword manager — add exact/broad negatives at campaign or ad-group scope |

All data comes from DB (no live ASA API on page load); freshness driven by last sync timestamp.

TanStack Query hooks: `useASACampaigns`, `useASAAdGroups`, `useASAPerformanceReport`, `useASASearchTermReport`, `useASAKeywords`, `useASANegativeKeywords`, `useASAPaidOrganicJoin`, `useAddNegativeKeywords`, `useASASync` — all in `frontend/src/lib/hooks.ts`.

## Settings Panel

ASA credential management lives in the Settings page (under the existing credentials tab). Users upload a `.p8` private key, enter `clientId`, `teamId`, `keyId`, then pick which Apple org to associate with which app.

## Security

- All credential fields (clientId, teamId, private key bytes) are Fernet-encrypted at rest (`app.core.security.encrypt_value`).
- Private key is decrypted in memory only for the duration of a token fetch, then discarded.
- `_own_credential_for_user` guards every credential-scoped MCP operation.
- Per-app endpoints run through `_get_verified_app` (same ownership chain as pricing/metadata).
- **Two scopes, both enforced in the query**: analytics reads filter on `credential_id IN (credentials owned by user_id)` (cross-tenant) *and* on the app. Verifying `app_id` upstream is not enough — a user with several apps under one ASA credential passes the credential filter for all of them. `search_term_report_rows` therefore joins `asa_search_terms → asa_ad_groups → asa_campaigns` and filters `asa_campaigns.app_id`; `performance_rows` and the `joins.py` queries filter `ASAMetricDaily.app_adam_id`. Both fail closed on NULL (`credential_id`, `asa_campaigns.app_id`). Regressions: `backend/tests/test_asa_analytics_scoping.py`.

## Edge Cases & Constraints

- **Token TTL**: access tokens expire 1h; client_secret JWT TTL is set to 2h so it outlives the token window. Cached per-process — parallel workers each maintain their own cache.
- **X-AP-Context header**: must match the orgId of the campaign being queried; `ASAClient` sets it per-call.
- **Apple rate limits**: 429 → backoff up to 5 retries (separate from the ASC 150ms throttle).
- **Date range**: search-term granularity data older than 90 days is not available from the ASA API.
- **adam_id linkage**: `ASACampaign.app_adam_id` (Apple's numeric app ID) is matched against `App.asc_app_id` to scope per-app queries; unlinked campaigns show in credential-level views only.
