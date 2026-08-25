---
id: 004
title: "Cache Apple Price Points in DB"
status: superseded
created: 2026-04-01
updated: 2026-08-26
tasks: []
---

# 004 - Cache Apple Price Points in DB

> **Superseded 2026-08-26**: the problem this spec targets (`get_price_points()` hanging on
> unfiltered ASC price-point fetches) is already solved by the filesystem-based `PricePointCache`
> (`backend/app/services/asc/price_point_cache.py`), the project's documented architecture choice
> (see CLAUDE.md — "Price point cache: filesystem-based ... not DB"). All five tasks below already
> have working equivalents against that cache; the `SubscriptionPricePoint` DB model this spec
> proposed was implemented but never wired up, and has been removed as dead code.

## Problem
`get_price_points()` fetches ALL ~180k subscription price points from ASC API (200/page = 900 pages), which hangs forever. The preview and prices endpoints block on this, making the pricing page unusable.

## Requirements
- Cache ASC price points in a local DB table
- Preview and prices endpoints read from DB cache (fast)
- Explicit "Sync from Apple" button triggers the heavy ASC fetch
- Generous timeouts (5 min) on sync — no artificial limits
- Current prices also synced on explicit request only (not on every page load)
- Page loads from cache instantly

## Architecture

### New Model: `SubscriptionPricePoint`
Table `subscription_price_points` — caches the available Apple price tiers per subscription+territory.

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto |
| subscription_id | FK subscriptions.id | indexed |
| territory_code | str(10) | e.g. "USA", "GBR" |
| currency_code | str(10) | e.g. "USD" |
| customer_price | float | |
| proceeds | float | |
| price_point_id | str(255) | ASC opaque ID |
| synced_at | datetime | when last fetched |

Unique constraint: (subscription_id, price_point_id)

### Endpoint Changes

**GET `/apps/{id}/subscriptions/{sub_id}/prices`** — Read from DB only. No ASC calls.

**POST `/apps/{id}/subscriptions/{sub_id}/prices/preview`** — Read price points from DB cache for nearest-price matching. No ASC calls.

**POST `/apps/{id}/subscriptions/{sub_id}/sync` (NEW)** — Sync both current prices and price points from ASC. Generous timeout (5 min). Deletes stale price points before inserting fresh ones.

### Frontend Changes

**PricingPage.tsx** — Add "Sync from Apple" button next to subscription selector. Shows loading state during sync. On success, refetch prices.

**hooks.ts** — Add `useSyncSubscriptionPrices()` mutation hook.

## Edge Cases & Risks
| Case | Mitigation |
|------|------------|
| Price points cache empty (first use) | Show "Sync from Apple" prompt, preview works without price matching |
| Sync takes 5+ minutes | Frontend shows progress, no timeout on backend |
| ASC API rate limits during sync | Existing retry logic in ASCClient handles 429s |
| Stale cache after Apple changes prices | User triggers manual sync; synced_at shown in UI |

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Add SubscriptionPricePoint model | dev | — | pending | backend/app/models/subscription.py |
| T2 | Add sync endpoint (prices + price points from ASC) | dev | T1 | pending | backend/app/api/v1/pricing.py |
| T3 | Refactor GET prices to read from DB only | dev | T1 | pending | backend/app/api/v1/pricing.py |
| T4 | Refactor preview to read price points from DB | dev | T1 | pending | backend/app/api/v1/pricing.py |
| T5 | Add frontend sync button + hook | dev | T2 | pending | frontend/src/pages/PricingPage.tsx, frontend/src/lib/hooks.ts |

## Acceptance Criteria
- [ ] Preview loads instantly from cache (no ASC calls)
- [ ] Prices page loads instantly from cache
- [ ] "Sync from Apple" fetches fresh data and updates cache
- [ ] Price matching works after sync (nearest_apple_price populated)
- [ ] No timeouts on sync endpoint
