# Apple Search Ads (ASA) Analytics — Design

**Status:** approved (brainstorm), pending implementation plan
**Date:** 2026-05-08
**Owner:** ASO-Light backend + frontend
**Tracks:** Search Ads vertical, paid + organic integration

---

## 1. Purpose

Surface Apple Search Ads campaign / ad-group / keyword / search-term
performance alongside the existing organic keyword tracking, ASO audit, and
visibility tooling. Make the paid+organic loop (find a high-converting search
term, decide whether to track it organically or block it as a negative) a
first-class action — both in the web UI and through the MCP tool surface so
LLM clients can drive it.

**v1 scope (deliberate):**

- Read-only on campaigns / ad groups / keywords / search terms.
- Full performance reporting (impressions, taps, installs, spend, CPI, CPA,
  conversion rate) at daily grain across campaign / ad-group / keyword /
  search-term dimensions.
- One mutation surface: add / remove **negative keywords** (the highest-leverage,
  lowest-blast-radius write — pairs naturally with search-term reports).

**Explicitly out of v1:** create / pause / modify campaigns, edit ad groups,
change keyword bids, edit budgets. ASA Basic (non-API) tier is unsupported.

---

## 2. Architecture

```
┌─ FastAPI ────────────────────────────────────────────────┐
│                                                          │
│   /api/v1/asa/credentials       ASA cred CRUD            │
│   /api/v1/asa/orgs              list orgs visible        │
│   /api/v1/apps/{id}/asa/*       per-app reports,         │
│                                  search terms, negatives │
│                                                          │
│   app/services/asa/                                      │
│   ├─ auth.py        ES256 client_secret + token cache    │
│   ├─ client.py      ASAClient (httpx, rate limited)      │
│   ├─ campaigns.py   campaigns / ad groups / keywords     │
│   ├─ search_terms.py search-term reports + negatives     │
│   ├─ reports.py     performance reports                  │
│   └─ sync.py        orchestrator → asa_* tables          │
│                                                          │
│   /mcp  ── 15 asa.* tools                                │
│         ── keywords.list_for_app gains with_paid flag    │
│         ── aso.aso_check gains paid-coverage column      │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Same shape as the existing ASC and RevenueCat verticals: encrypted credential
at rest, service module per surface, snapshot tables in DB, MCP tools as thin
wrappers over service classes. No new architectural patterns.

### 2.1 Authentication (Apple Search Ads Advanced)

ASA Advanced uses OAuth2 client credentials with an ES256-signed
`client_secret` JWT. Identical mechanism to ASC, different audience. The user
provides four pieces:

- `clientId` (e.g. `SEARCHADS.xxxxxxx-xxxx-xxxx-xxxx-xxxxxxxx`)
- `teamId` (Apple developer team)
- `keyId`
- private key PEM (P256 / ES256) — generated in the ASA UI when creating an
  "API user"

**Token flow (per ASAClient instance):**

1. Build `client_secret` JWT with claims `{sub: client_id, aud: "https://appleid.apple.com",
   iss: team_id, exp: now+30min}`, signed ES256, header `{alg: ES256, kid: key_id}`.
2. POST `https://appleid.apple.com/auth/oauth2/token` with
   `grant_type=client_credentials`, `client_id`, `client_secret`,
   `scope=searchadsorg`.
3. Cache the resulting access token (1h TTL) in-process keyed by
   `credential_id`. Refresh at T-5min or on 401.
4. Use `Authorization: Bearer <access_token>` plus `X-AP-Context: orgId=<n>`
   header on org-scoped calls.

Private keys are Fernet-encrypted at rest and decrypted in memory only for
the lifetime of `ASAClient.from_credential` — same convention as
`ASCClient.from_credential`.

### 2.2 Rate limiting & retry

- 150ms minimum interval between requests (same as `ASCClient`).
- Exponential, jittered backoff on 429; max 6 retries.
- One automatic retry on 401 (after token refresh); a second 401 raises
  `ASAAPIError`.
- All ASA errors map to `ToolError(detail)` in MCP tools — no HTTP framing
  leaks to LLM clients.

---

## 3. Data model

Nine new tables (eight data tables + one operations log). SQLAlchemy 2.0
mapped style. Fernet-encrypted secrets at rest. New models register via
`backend/app/models/__init__.py` and auto-create on startup in dev (SQLite,
`Base.metadata.create_all`). For production PostgreSQL the implementation
plan generates one Alembic revision covering all nine tables.

### 3.1 `asa_credential` (per user)

```
id, user_id (FK users CASCADE), name,
client_id_ciphertext, team_id_ciphertext, key_id (cleartext),
private_key_ciphertext,
last_synced_at, created_at, updated_at
```

`key_id` is the only id stored cleartext — non-secret and shown in the
"Credentials" UI for identification.

### 3.2 `asa_org`

```
id, credential_id (FK asa_credential CASCADE),
asa_org_id (Apple's numeric id), name, currency, timezone, role,
created_at, updated_at
UNIQUE (credential_id, asa_org_id)
```

One credential → many orgs (returned by `GET /me/acl`).

### 3.3 `asa_campaign`

```
id, org_id (FK asa_org CASCADE),
asa_campaign_id, app_id (FK apps, nullable),
app_adam_id, name, status, supply_sources (JSON list),
daily_budget_amount, daily_budget_currency,
storefronts (JSON list of country codes),
archived_at (nullable),
created_at, updated_at
UNIQUE (org_id, asa_campaign_id)
```

`app_id` is nullable: ASA orgs may advertise apps not yet in our local DB.
We bind to a local App on `app_adam_id` match — lazy bind at sync time when
the local App is later created via `apps.sync`.

### 3.4 `asa_ad_group`

```
id, campaign_id (FK asa_campaign CASCADE),
asa_ad_group_id, name, status,
default_bid_amount, default_bid_currency,
age_range (JSON), gender, device_class,
archived_at (nullable),
created_at, updated_at
UNIQUE (campaign_id, asa_ad_group_id)
```

### 3.5 `asa_keyword`

```
id, ad_group_id (FK asa_ad_group CASCADE),
asa_keyword_id, text, match_type (BROAD|EXACT),
bid_amount, bid_currency, status,
archived_at (nullable),
created_at, updated_at
UNIQUE (ad_group_id, asa_keyword_id)
```

### 3.6 `asa_negative_keyword`

```
id,
campaign_id (FK asa_campaign, nullable),
ad_group_id (FK asa_ad_group, nullable),
asa_negative_keyword_id, text, match_type,
scope (CAMPAIGN|AD_GROUP),
created_at
CHECK ((campaign_id IS NULL) <> (ad_group_id IS NULL))
```

Exactly one of `campaign_id` / `ad_group_id` is non-null, depending on
scope. ASA supports both campaign-level and ad-group-level negatives.

### 3.7 `asa_search_term`

```
id, ad_group_id (FK asa_ad_group CASCADE),
text, match_type, source (SEARCHTERM|RAW),
archived_at (nullable),
created_at, updated_at
UNIQUE (ad_group_id, text, match_type)
```

Search terms have no Apple id — Apple does not surface one. Identity is the
`(ad_group_id, text, match_type)` triple.

### 3.8 `asa_metric_daily` (the fact table)

```
id,
dim_kind ENUM(CAMPAIGN|AD_GROUP|KEYWORD|SEARCH_TERM),
dim_id (FK by kind, enforced in app),
app_adam_id (denormalized for fast app-scoped queries),
date (UTC day),
storefront (country code, nullable),
impressions, taps, installs, new_downloads, redownloads,
spend_amount, spend_currency,
avg_cpa_amount, avg_cpt_amount, ttr, conversion_rate,
created_at, updated_at
UNIQUE (dim_kind, dim_id, date, storefront)
INDEX (app_adam_id, date)
```

Single fact table with a `dim_kind`/`dim_id` polymorphic FK keeps the schema
small. `app_adam_id` is denormalized so app-scoped reports avoid a 4-table
join through campaign. `storefront` is a country code; null means "rolled up
across all storefronts."

### 3.9 `asa_sync_operation` (operations log)

```
id, credential_id (FK), user_id, status,
asc_steps_json (per-step status, mirrors CloneOperation pattern),
error_log_json, started_at, completed_at, full_backfill (bool)
```

Mirrors the existing `CloneOperation` pattern. Lets the UI poll for partial
failures and retry.

### 3.10 Soft delete

When an item disappears from ASA listings, set `archived_at = now()` instead
of hard-deleting. Historical metrics in `asa_metric_daily` continue to point
at the row. The UI filters `archived_at IS NULL` by default but can show
archived items on demand.

### 3.11 Paid + organic keyword join (computed view, not materialized)

```sql
SELECT kt.term,
       kt.last_rank AS organic_rank,
       SUM(m.impressions) AS paid_impressions_30d,
       SUM(m.taps)        AS paid_taps_30d,
       SUM(m.installs)    AS paid_installs_30d,
       SUM(m.spend_amount) AS paid_spend_30d
FROM keyword_tracking kt
LEFT JOIN asa_keyword ak
  ON LOWER(ak.text) = LOWER(kt.term)
  AND ak.archived_at IS NULL
LEFT JOIN asa_metric_daily m
  ON m.dim_kind = 'KEYWORD' AND m.dim_id = ak.id
  AND m.date >= CURRENT_DATE - INTERVAL '30 days'
WHERE kt.app_id = :app_id
GROUP BY kt.term, kt.last_rank
```

Implementation lives in `app/services/asa/joins.py`.

---

## 4. Sync flow

`asa.sync(credential_id, full=False)` orchestrates the fetch:

```
1. POST /me/acl                            → upsert asa_org rows
2. for each org with X-AP-Context = orgId:
     POST /campaigns/find                  → upsert asa_campaign,
                                              archive missing
     for each campaign:
         POST /campaigns/{id}/adgroups/find → upsert asa_ad_group
         for each ad_group:
             POST /campaigns/{id}/adgroups/{id}/targetingkeywords/find
                                            → upsert asa_keyword
             POST /campaigns/{id}/adgroups/{id}/negativekeywords/find
                                            → upsert asa_negative_keyword
3. Determine since:
     - full=true  → CURRENT_DATE - 90 days
     - full=false → max(last_synced_at - 1 day, CURRENT_DATE - 90 days)
4. For each (grain, dim_kind) in (campaign, ad_group, keyword, search_term):
     POST /reports/<path> with timeRange=[since, today], granularity=DAILY
     UPSERT into asa_metric_daily on
       (dim_kind, dim_id, date, storefront)
5. Update asa_credential.last_synced_at.
6. asa_sync_operation.status = done | partial | failed.
```

Idempotent. Resumable. Each step's status flows into the
`asa_sync_operation` row so the UI / MCP can show "campaigns ✓, ad_groups ✓,
keyword_metrics partial, search_term_metrics ✗" and offer a retry.

**Incremental window:** default sync re-fetches from `last_synced_at - 1 day`
to today (one day overlap to catch late-arriving install attributions). A
`full=true` flag triggers a 90-day backfill — used on first connect or after
data corruption.

---

## 5. MCP tool surface

15 new `asa.*` tools. Two `keywords.*` / `aso.*` enhancements.

### 5.1 New tools

```
# Credentials & connectivity (4)
asa.list_credentials() -> [ASACredentialOut]
asa.test_credential(credential_id) -> {ok, orgs_visible}
asa.delete_credential(credential_id)
asa.list_orgs(credential_id) -> [ASAOrgOut]

# Listings — read from cache (5)
asa.list_campaigns(app_id=None, org_id=None, status=None) -> [...]
asa.get_campaign(campaign_id)
asa.list_ad_groups(campaign_id)
asa.list_keywords(ad_group_id)
asa.list_negative_keywords(campaign_id=None, ad_group_id=None)

# Reports — read from cache (3)
asa.performance_report(app_id, grain, time_range,
                       storefront=None, status=None)
    # grain ∈ {CAMPAIGN, AD_GROUP, KEYWORD}
asa.search_term_report(app_id, time_range,
                       ad_group_id=None, min_impressions=None)
asa.paid_organic_join(app_id, time_range)

# Insights — rule-based, computed in DB (2)
asa.suggest_organic_keywords_to_track(app_id, time_range,
                                      min_taps=20)
asa.suggest_negative_candidates(app_id, time_range,
                                min_spend=10.0, max_conv_rate=0.005)

# Writes (2)
asa.add_negative_keywords(scope, scope_id, keywords)
    # scope ∈ {CAMPAIGN, AD_GROUP}; keywords = [{text, match_type}]
asa.remove_negative_keyword(negative_keyword_id)

# Sync (1)
asa.sync(credential_id, full=False)
```

### 5.2 Existing-tool enhancements

- `keywords.list_for_app(app_id, with_paid=False)` — when `with_paid=True`
  and ASA is connected, each row gains
  `paid_metrics_30d: {impressions, taps, installs, spend_amount, spend_currency} | null`.
- `aso.aso_check(app_id)` — gains a `paid_coverage` advisory section listing
  tracked organic keywords without a matching ASA bid (a common ASO blind
  spot).

### 5.3 Naming / contract conventions

- All `asa.*` tools follow the existing snake_case-with-prefix convention
  (`asa.list_campaigns`, not `asa.campaigns.list`).
- Pydantic schemas live in `backend/app/schemas/asa.py`. Reused as-is by REST
  and MCP tools.
- Errors raise `ToolError(human_message)` — no HTTPException leakage.

### 5.4 No AI in v1

The two `suggest_*` tools are SQL with thresholds. The MCP client (the LLM)
is itself the intelligence layer; we give it raw and aggregated data and
let it reason. Claude-based ranking can be layered later if rule-based
proves inadequate.

---

## 6. UI surface

### 6.1 New page: `/apps/:id/paid-search`

Tabs:

- **Overview** — 90-day spend + installs + CPI; blended paid+organic install
  total; trend sparklines.
- **Campaigns** — table with rollup metrics; click → drill into ad groups.
- **Keywords** — paid keywords with bid + 30-day perf; row action: add as
  negative.
- **Search terms** — table with two CTAs per row:
  `Track as organic` → calls `keywords.add` for the same app.
  `Add as negative`  → calls `asa.add_negative_keywords` (scope picker:
  campaign vs ad group).
- **Negatives** — list (filterable by scope), bulk add, single remove.

### 6.2 Existing-page enhancements

- **Settings page**: "Search Ads Credentials" panel, sibling to the Personal
  Access Tokens panel. Upload PEM + ids, list, test, delete. Plaintext
  secrets are never re-shown after upload.
- **Keywords page**: a "Paid" toggle in the table header. When ASA is
  connected, toggling on adds 30-day paid metric columns next to each
  tracked keyword. Off by default; preference saved per app.
- **ASO Check page**: a new "Paid coverage" advisory line at the bottom,
  listing tracked organic keywords with no matching ASA bid.

### 6.3 Data layer

All TanStack Query hooks added to the existing
`frontend/src/lib/hooks.ts`, grouped at the bottom in an "Apple Search Ads"
section. Naming: `useASACredentials`, `useASACampaigns(appId)`,
`usePaidOrganicJoin(appId, range)`, `useASASync()`, etc.

---

## 7. Error handling & edge cases

- **Cred upload validation**: post-upload, run `asa.test_credential`
  synchronously; reject the cred with 400 if Apple's `/me/acl` returns 401
  or empty.
- **Token refresh**: `ASAClient` catches 401 once, invalidates token cache,
  retries. Second 401 → `ASAAPIError`.
- **Rate limits (429)**: shared backoff loop with jitter, max 6 retries;
  after exhaustion surface
  `ToolError("ASA rate-limited; retry in N seconds")`.
- **Sync partial failure**: `ASASyncOperation` row tracks per-step status;
  retry endpoint re-runs only the failed steps (idempotent).
- **Orphaned campaigns** (ASA campaign for an app not in our DB):
  `asa_campaign.app_id = NULL`. UI shows a "Bind to local app" CTA when an
  `apps.sync` later imports the matching `adam_id`.
- **Currency mismatch**: ASA reports in the org's billing currency. Spend
  columns always include `*_currency`. UI displays the currency badge per
  org. We do NOT auto-convert — preserves source-of-truth fidelity.
- **Search-term identity churn**: same text but different match_type is a
  different row by design (Apple treats them as different). Surfaced in
  the search-term table as separate rows.

---

## 8. Authorization model

Per-user PAT authorization unchanged from existing patterns:

- ASA credentials owned by `user_id` (chained via `asa_credential.user_id`).
- ASA orgs / campaigns / etc. accessible only via a credential owned by
  the user.
- App-scoped tools resolve `App` via existing `resolve_app` helper, then
  filter ASA campaigns by `app_adam_id`.
- A user's PAT cannot read another user's ASA data — same chain as ASC.

---

## 9. Testing

- **Unit (auth)**: `auth.build_client_secret` produces a JWT with the
  correct claims, header (alg=ES256, kid), and audience.
- **Unit (client)**: `httpx.MockTransport` for header injection,
  401-then-retry, 429 backoff, token cache hit.
- **Unit (sync)**: idempotency — two consecutive syncs against the same
  fixture produce identical DB state; soft-delete fires when an entity
  disappears between syncs; orphaned campaign binds when the local App
  appears.
- **Unit (insights)**: `suggest_organic_keywords_to_track` and
  `suggest_negative_candidates` against a seeded DB confirm the SQL logic.
- **MCP smoke**: all 15 `asa.*` tools register on FastMCP boot; tool count
  jumps from 123 → 138 (no regressions on existing tools).
- **Manual validation**: a checklist for a one-time real-ASA-org test
  during implementation (covers cred upload, sync of a small org, paid
  organic join, negative-keyword add+remove).

No live ASA tests in CI — pre-existing project convention (no test
sandbox available without an ASA org).

---

## 10. Out of scope (v1)

- Campaign / ad-group / keyword **mutation** beyond negative keywords.
- Budget / bid management.
- ASA Basic tier (no API).
- Hourly metrics fact table.
- Auto-conversion to a single display currency.
- AI-powered insight ranking (beyond rule-based `suggest_*`).
- Cross-org consolidation views (each org displayed individually for v1).

These are deliberately excluded to keep v1 shippable. Each is a single
follow-up spec when prioritized.

---

## 11. Files to create / modify

### Create

```
backend/app/services/asa/__init__.py
backend/app/services/asa/auth.py
backend/app/services/asa/client.py
backend/app/services/asa/campaigns.py
backend/app/services/asa/search_terms.py
backend/app/services/asa/reports.py
backend/app/services/asa/sync.py
backend/app/services/asa/joins.py
backend/app/services/asa/errors.py
backend/app/models/asa.py            (all 9 models in one module)
backend/app/schemas/asa.py
backend/app/api/v1/asa.py            (mounted under /api/v1/asa)
backend/app/api/v1/asa_app.py        (per-app routes under /apps/{id}/asa)
backend/app/mcp/tools/asa.py
frontend/src/pages/PaidSearchPage.tsx
frontend/src/components/asa/...      (table components)
```

### Modify

```
backend/app/main.py                  (no change — routes auto-mount via __init__.py)
backend/app/api/v1/__init__.py       (register asa + asa_app routers)
backend/app/models/__init__.py       (import asa models)
backend/app/mcp/server.py            (import asa tool module)
backend/app/mcp/tools/keywords.py    (add with_paid arg to list_for_app)
backend/app/mcp/tools/aso.py         (add paid_coverage to aso_check output)
frontend/src/App.tsx                 (route for /apps/:id/paid-search)
frontend/src/pages/SettingsPage.tsx  (Search Ads Credentials panel)
frontend/src/pages/KeywordsPage.tsx  (Paid toggle + columns)
frontend/src/pages/AsoCheckPage.tsx  (Paid coverage line)
frontend/src/lib/hooks.ts            (asa.* hooks)
backend/app/core/security.py         (no change — reuse Fernet helpers)
```

### Reuse (no changes)

- `app.core.security.{encrypt_value, decrypt_value}` — Fernet helpers.
- `app.api.v1._deps.{_get_verified_app, _get_asc_client_for_app}` —
  ownership chain (ASA mirrors with `_get_asa_client_for_credential`).
- `app.mcp.context.{resolve_app, get_user_id, session_scope}` — MCP auth
  helpers.
- Rate-limit + 429-backoff pattern from `app.services.asc.client` (currently
  inlined as module-level constants + `asyncio.sleep`). Implementation plan
  decides whether to lift it into a shared module or duplicate the ~30 LOC
  inside `ASAClient` — duplicating is acceptable for v1.

---

## 12. Verification (acceptance criteria)

1. **Boot**: backend starts cleanly with new tables auto-created;
   `from app.main import app` loads without error.
2. **Tool count**: `mcp.list_tools()` returns 138 (was 123).
3. **Cred lifecycle**: `POST /api/v1/asa/credentials` accepts a valid
   PEM + ids, returns 201; `asa.test_credential` returns
   `{ok: true, orgs_visible: N}`; `DELETE` removes it.
4. **Sync**: `asa.sync(credential_id)` populates orgs, campaigns,
   keywords, search terms, and metrics for the last day. Re-running
   produces identical row count (idempotent).
5. **Reports**: `asa.performance_report(app_id, grain='KEYWORD',
   time_range=last_30d)` returns expected metrics structure.
6. **Search-term report**: matches the ASA UI for a hand-checked ad
   group.
7. **Paid+organic join**: tracked organic keyword that also exists in
   ASA returns merged metrics; one not in ASA returns `null` for paid
   columns.
8. **Negative-keyword add/remove**: adds a CAMPAIGN-scoped negative,
   visible in `asa.list_negative_keywords` and in the ASA UI.
9. **Insight tools**: `suggest_organic_keywords_to_track` returns ASA
   search terms above the `min_taps` threshold not in
   `keyword_tracking`. `suggest_negative_candidates` returns search
   terms above `min_spend` with conv-rate below the threshold.
10. **UI**: new Paid Search page renders, drill-down works, search-term
    "Track as organic" CTA round-trips through `keywords.add` and shows
    up in the Keywords page.
11. **Permission boundary**: user A's PAT cannot read user B's
    `asa_credential` or any data downstream of it (same chain as ASC).
12. **No regressions**: all pre-existing tests still pass; existing 123
    MCP tools still register.

---

## 13. Open questions

None blocking. Items the implementation may surface and that we'll resolve
inline:

- Exact `archived_at` cleanup policy (keep forever vs. compact metrics
  older than 90 days).
- Whether `asa_metric_daily` needs a partial index for
  `dim_kind='KEYWORD'` once row counts grow.
- Whether to expose the `asa_sync_operation` row to MCP as
  `asa.list_sync_operations` (probably yes, mirrors clone-operations).

---

## See also

- [docs/006-product-swap-ios-integration.md](../../006-product-swap-ios-integration.md)
- [docs/007-mcp-integration.md](../../007-mcp-integration.md) — MCP server,
  PAT lifecycle, tool reference
- `backend/app/services/asc/client.py` — pattern to mirror for `ASAClient`
- `backend/app/services/asc/clone.py` — operation/partial-status pattern to
  mirror for `ASASyncOperation`
- `backend/app/api/v1/credentials.py` — credential-CRUD pattern
