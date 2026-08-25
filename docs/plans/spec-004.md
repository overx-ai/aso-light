---
spec: docs/specs/004-cache-apple-price-points.md
status: STOPPED (spec unimplementable — already solved differently)
---

## Approach

The problem this spec describes — `get_price_points()` fetching all ~180k
price points unfiltered (200/page × 900 pages, hangs forever) and the
preview/prices endpoints blocking on it — **has already been fixed in this
codebase**, but via a different, already-shipped, already-documented
architecture than the one this spec prescribes. Implementing the spec
literally (wiring `SubscriptionPricePoint` DB rows into the sync/prices/
preview endpoints) would mean tearing out a working system to replace it
with a worse one. Concretely, verified by reading the current code:

1. **`app/services/asc/pricing.py::get_price_points()`** already accepts
   `territory_code` and the only caller (`PricePointCache.fetch_and_cache`)
   always passes one. So the root-cause bug — the unbounded, unfiltered
   180k-row fetch — no longer exists. Every ASC call for tiers is scoped to
   one territory.

2. **`app/services/asc/price_point_cache.py` (`PricePointCache`)** is a
   filesystem-based cache (`backend/.cache/price_points/{product_type}/
   {alpha2}.json`) that is explicitly documented (in its own module
   docstring and in `CLAUDE.md`'s Architecture Decisions section: *"Price
   point cache: filesystem-based under `backend/.cache/price_points/`, not
   DB — per-territory JSON files"*) as **app-wide / product-type-wide**,
   not per-subscription. The rationale recorded in the docstring: Apple's
   tier ladder is identical across every subscription/IAP on the App
   Store, so caching it once per `product_type` (not per `subscription_id`
   as the spec's `SubscriptionPricePoint` table would) avoids re-fetching
   the same ~175-territory ladder for every subscription in every app —
   the first "Sync Price Tiers" click anywhere populates it for everyone.
   `price_point_id`s are computed locally per-product via
   `compute_price_point_id()` rather than stored per row.

3. **Every endpoint the spec calls out is already done, task-by-task**,
   just against the filesystem cache instead of a DB table:
   - T3 (`GET .../prices` DB-only) — `get_subscription_prices()` in
     `app/api/v1/pricing.py` reads only `SubscriptionPrice` (DB) + the
     filesystem tier cache for currency lookup; makes zero ASC calls.
   - T2 (sync endpoint) — split into two explicit, generous-timeout,
     on-demand endpoints instead of one combined one:
     `POST .../subscriptions/{id}/sync` (current prices → DB,
     `SubscriptionPrice` table, ~2s) and
     `POST .../subscriptions/{id}/price-points/sync` (tier ladder → 
     filesystem cache, per-territory, resumable — `fetch_and_cache_all`
     skips already-cached territories on retry).
   - T4 (preview DB-only) — `preview_subscription_prices()` reads current
     prices from DB and tiers from `PricePointCache`; zero ASC calls.
   - T5 (frontend sync button + hook) — `frontend/src/lib/hooks.ts` already
     has `useSyncSubscriptionPrices()` and `useSyncPricePoints()`;
     `frontend/src/pages/PricingPage.tsx` already renders "Sync Price
     Tiers" / sync buttons wired to both, with loading state.
   - The IAP side has the exact same pattern (`sync_iap_prices`,
     `sync_iap_price_points`, IAP preview) — also already done.
   - MCP tools mirror this too: `pricing_sync_subscription_price_points`,
     `pricing_subscription_price_points_status` in
     `app/mcp/tools/pricing.py` — both use `PricePointCache`, not the DB
     table.

4. **T1 (the `SubscriptionPricePoint` model) exists but is dead code.**
   `app/models/subscription.py` defines it, and its table is present in
   `alembic/versions/000_base_schema.py` (already migrated), but
   `grep -rn "SubscriptionPricePoint\b" app/` turns up only the class
   definition and its own `__repr__` — no query, insert, or reference
   anywhere else in the app. It appears to be a scaffold from an earlier,
   abandoned attempt at this exact spec that was superseded by the
   filesystem-cache design before being wired up or removed.

Given this, implementing the spec as written would require either:
(a) building a second, parallel, per-subscription DB cache alongside the
already-working, already-more-efficient app-wide filesystem cache
(duplication, contradicts DRY and the recorded architecture decision), or
(b) ripping out `PricePointCache` and every caller across subscriptions,
IAPs, and MCP tools to replace it with the DB table (a large, high-risk
rewrite of working, tested, documented functionality — not "cache Apple
price points in a DB table," but "replace the pricing cache subsystem").
Both silently redesign a decision that's already been made and documented
in `CLAUDE.md`. Per the /tick instructions, that's a STOP condition, not a
judgment call to make unilaterally in an autonomous loop.

## Sequence

(Investigation only — no implementation performed.)
1. Read `backend/app/api/v1/pricing.py` (prices/preview/sync endpoints).
2. Read `backend/app/models/subscription.py` (confirm `SubscriptionPricePoint` exists).
3. Grep for `SubscriptionPricePoint` / `subscription_price_points` usage across `backend/app/` — found only the model definition and the base-schema migration; no callers.
4. Read `backend/app/services/asc/price_point_cache.py` — confirmed the filesystem cache is the real, documented, working replacement for what the spec's DB table would do.
5. Confirmed `get_price_points()` already filters by `territory_code`, so the spec's root-cause bug (unbounded 180k-row fetch) is already fixed.
6. Confirmed frontend (`hooks.ts`, `PricingPage.tsx`) already has sync hooks/buttons for both current prices and price tiers.
7. Concluded the spec is superseded; stopped before writing any code.

## Files

None modified. Files read during investigation:
- `backend/app/api/v1/pricing.py`
- `backend/app/models/subscription.py`
- `backend/app/services/asc/price_point_cache.py`
- `backend/app/services/asc/pricing.py` (`get_price_points`)
- `backend/app/mcp/tools/pricing.py`
- `backend/alembic/versions/000_base_schema.py`
- `frontend/src/lib/hooks.ts`
- `frontend/src/pages/PricingPage.tsx`

## Tests first

Not written — no implementation was started once the spec was determined
to be already superseded by shipped, working code.

## Risks

- The dead `SubscriptionPricePoint` model + its migrated-but-unused table
  is latent confusion for future readers (looks like an in-progress
  feature). Recommend either: (a) marking spec-004 as superseded/closed
  with a note pointing at `PricePointCache`, or (b) a follow-up cleanup
  spec to drop the unused model + a migration to drop the table — but
  that's a product/architecture decision for a human, not something to
  do unilaterally here.
- If there *is* a reason the DB-table approach is still wanted (e.g. to
  get per-subscription accurate `proceeds` cached without needing an
  ASC call, or to survive filesystem cache loss on redeploy), that
  wasn't evident from the code or docs — worth confirming with whoever
  approved this spec before re-scoping it.

## Deviations

(none — no implementation was performed)
