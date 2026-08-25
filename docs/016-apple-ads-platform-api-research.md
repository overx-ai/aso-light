# 016 - Apple Ads Platform API (Research)

**Prerequisites**: [011 - Apple Search Ads Analytics](011-apple-search-ads-analytics.md)
**Related**: [002 - ASC Integration](002-asc-integration.md)

> **Status: research only.** No code, schema, or credential changes were made as
> part of this doc. It's a snapshot of Apple's new API for whenever migration
> work gets scheduled — see [Not covered](#not-covered) below.

## Summary

Apple published a new **Apple Ads Platform API** (v1.0, docs live as of
August 2026: https://developer.apple.com/documentation/apple-ads-platform-api).
It's a single REST API unifying **App Store ads and Apple Maps ads** under one
surface — broader in scope than the API ASO-Light integrates today.

Per its own changelog, this API **supersedes the Apple Ads Campaign Management
API** — the `v5` API our [ASA analytics module](011-apple-search-ads-analytics.md)
currently uses — with a **sunset date of January 26, 2027** for the old API.
That's a real, dated deprecation (about 5 months out from when this doc was
written), not a hypothetical.

## Comparison: current (v5) vs. new (Platform API v1.0)

| | **Current — v5 Campaign Mgmt API** | **New — Apple Ads Platform API v1.0** |
|---|---|---|
| Base URL | `https://api.searchads.apple.com/api/v5` (`backend/app/services/asa/client.py:29`) | `https://api.ads.apple.com/v1/` |
| Auth mechanism | OAuth2 client-credentials, ES256-signed JWT `client_secret`, scope `searchadsorg` (`backend/app/services/asa/auth.py:19-53`) | **Same mechanism**: P-256/ES256 key pair, JWT `client_secret` (`iss=teamId`, `sub=clientId`, `aud=https://appleid.apple.com`), token endpoint `https://appleid.apple.com/auth/oauth2/token`, scope `searchadsorg`, token TTL 3600s |
| Account-scoping header | `X-AP-Context: orgId=<orgId>` (`client.py:141-143`) | `X-AP-Context: adAccountId=<adAccountId>` — **different id concept** (org → ad account); existing `ASAOrg` rows would need a re-linking step, not confirmed 1:1 |
| Query pattern | Existing client already POSTs to `/query`-style endpoints per v5 convention | `POST /v1/{resource}/query` with `filters` (field/operator/value), `sorting` (field/order), `pagination` (`offset`, `pageSize`, `fetchTotalCount`) — structurally similar; **not diffed field-by-field** against the current query builder |
| Response envelope | Not diffed in this pass | Success: `{"result": [...], "pagination": {"offset","pageSize","totalCount"}}`; Error: `{"error": {"code","message","details"}}` |
| Error codes | Not diffed | `400 bad_request`, `401 unauthorized`, `403 forbidden`, `404 not_found`, `429 rate_limit_exceeded`, `500 internal_server_error` |
| Scope | Search Ads (App Store) only | App Store **and Apple Maps ads**, plus **Product Pages, Bulk Operations, Budget Orders, Insights, Recommendations, Suggestions, Change History** — capabilities beyond what ASO-Light exposes today |

## Current integration inventory

For when a migration is scheduled, this is what touches ASA today (all
confirmed present on `feat/product-page-optimization`):

- **Service layer**: `backend/app/services/asa/{client,auth,sync,campaigns,reports,analytics,joins,cpp_ads,errors}.py`
- **Models**: `backend/app/models/asa.py` — `ASACredential → ASAOrg → ASACampaign → ASAAdGroup → {ASAKeyword, ASANegativeKeyword, ASASearchTerm}`, plus the polymorphic `ASAMetricDaily` fact table (tenant-scoped via `credential_id`) and `ASASyncOperation`
- **MCP tools**: `backend/app/mcp/tools/asa.py` (20 tools registered)
- **REST routes**: `backend/app/api/v1/asa.py` (credential-scoped, prefix `/asa`), `backend/app/api/v1/asa_app.py` (per-app, prefix `/apps`)
- **Frontend**: `frontend/src/pages/PaidSearchPage.tsx` (Overview/Campaigns/Keywords/Search Terms/Negatives tabs, DB-only reads — see [011](011-apple-search-ads-analytics.md#frontend--paid-search-page))
- **Existing architecture doc**: [011 - Apple Search Ads Analytics](011-apple-search-ads-analytics.md)

## Open / unverified

Flagged explicitly as unverified — do not treat as fact without checking against
a live tenant or the full endpoint reference before acting on them:

- Whether `/v1/{resource}/query` filter/sort semantics are a drop-in match for
  the current v5 query builder (`client.py` / `reports.py`), or differ
  field-by-field.
- Whether existing `ASAOrg` records map 1:1 to the new `adAccountId` concept,
  or require a re-linking/re-auth step per credential.
- The new-API-only resources (Product Pages, Bulk Operations, Budget Orders,
  Apple Maps ads, Insights/Recommendations/Suggestions, Change History) are
  listed from the doc's table of contents only — not explored in depth, and
  none of them map to an existing ASO-Light feature today.

## Not covered

By design, this research pass does not include: a migration timeline, code
changes, or credential/schema proposals. When migration work is scheduled,
start from the *Current integration inventory* above and the open items list.
