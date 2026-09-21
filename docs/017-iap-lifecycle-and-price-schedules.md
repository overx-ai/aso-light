---
status: current
created: 2026-09-22
updated: 2026-09-22
---

# 017 - IAP Lifecycle + Price Schedule Semantics

> **TL;DR** — In-app purchases can now be created, renamed and deleted through REST and MCP, and a
> brand-new IAP is priceable immediately: create → preview → apply, no sync step. Two Apple quirks are
> load-bearing here — a never-priced IAP 404s its price schedule with a body identical to a deleted
> IAP's, and the attribute is spelled `familySharable`, not `familyShareable`.

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md), [001 - Pricing System](001-pricing-system.md)
**Related**: [005 - Subscription Management](005-subscription-management.md) (the subscription-side twin of this doc), [004 - Localization Management](004-localization-management.md), [007 - MCP Integration](007-mcp-integration.md)

## Overview

Subscriptions had a full write lifecycle; IAPs did not. `ASCPricingService.create_iap` existed but was
reachable only from the clone path — there was no REST route, no MCP tool, and no update or delete at
all. The practical consequence: an agent asked to add six consumables had to fall back to typing them
into the App Store Connect web UI by hand.

This doc covers the write paths that closed that gap, and the two Apple behaviours that make the IAP
pricing path differ from the subscription one in ways that are not obvious from the API reference.

## Out of Scope

- Submit-for-review and state transitions — manual in ASC, same as subscriptions ([005](005-subscription-management.md))
- App-level (paid app) price schedules — see [spec 011](specs/011-app-price-schedules.md), deliberately `draft`
- Promotional offers / offer codes

## Write Paths

| Operation | REST | MCP tool | Notes |
|-----------|------|----------|-------|
| Create | `POST /apps/{id}/iaps` | `pricing_create_iap` | Returns a shell — needs a localization and a price before submission |
| Update | `PATCH /apps/{id}/iaps/{iap_id}` | `pricing_update_iap` | `name`, `review_note`, `family_sharable` only |
| Delete | `DELETE /apps/{id}/iaps/{iap_id}` | `pricing_delete_iap` | State-dependent at Apple; refusal surfaced verbatim |
| Delete localization | `DELETE /apps/{id}/iaps/{iap_id}/localizations/{loc_id}` | `pricing_delete_iap_localization` | Membership asserted first — see Ownership below |

**Files**: `backend/app/services/asc/pricing.py` (service), `backend/app/api/v1/pricing.py` (routes),
`backend/app/mcp/tools/pricing.py` (tools), `backend/app/schemas/pricing.py` (`IAPCreate` / `IAPUpdate`).

### Immutables

`productId` and `inAppPurchaseType` are fixed at Apple once the IAP exists. `IAPUpdate` simply has no
field for them — the same technique `SubscriptionUpdate` uses for `productId` / `subscriptionPeriod`,
so the contract is enforced by the schema rather than by a runtime check that can be forgotten.

IAP mutations are **v2-only** (`/v2/inAppPurchases/{id}`), but their localizations are still v1
(`/v1/inAppPurchaseLocalizations/{id}`). Mixing these up produces a confusing 404.

### Family sharing

Apple spells the attribute **`familySharable`** — one `e`. The codebase used `familyShareable` in four
places, so reads were rejected outright (`'familyShareable' is not a valid field name`) and writes were
silently ignored: IAP family sharing had never worked. Confirmed against the live API, where the IAP
resource returns `"familySharable": false`.

It is honoured only on `NON_CONSUMABLE`. Both paths reject it elsewhere rather than dropping it quietly:

- create — `IAPCreate`'s validator, which can see `iap_type` in the request
- update — `_assert_family_sharing_supported`, which reads the locally mirrored `iap_type`. That is
  authoritative precisely *because* the field is immutable, so no ASC round-trip is needed, and the
  check runs before the client is opened so a rejected request costs no `.p8` decrypt.

### Local mirror

`_upsert_iap_row(session, app, iap_data)` in `backend/app/api/v1/pricing.py` is the single copy of the
ASC→DB mirror. Three call sites share it — the REST list, the REST create, and the MCP list — so a
create can no longer leave the IAP invisible locally until the next list call happens to run.

## Price Schedule Semantics

### A never-priced IAP has no schedule at all

`GET /v2/inAppPurchases/{id}/iapPriceSchedule` **404s** when the IAP has never been priced. The body is
byte-identical to the one returned for an IAP that does not exist — verified live:

```
FRESH IAP  404  "There is no resource of type 'inAppPurchasePriceSchedules' with id '6814641893'"
BOGUS ID   404  "There is no resource of type 'inAppPurchasePriceSchedules' with id '9999999999'"
```

So the error text **cannot** distinguish the two cases. `get_iap_price_schedule` instead probes
`get_iap_detail`: if the IAP answers, there is simply no schedule yet and it returns `[]`; if that probe
404s too, the original error propagates. A stale local row pointing at a deleted IAP therefore still
fails loudly instead of silently reporting "no prices".

### Why apply needs a guard at all

Apple replaces the **entire** `iapPriceSchedule` on every apply. The apply path therefore re-submits
untouched territories from the local `IAPPrice` cache — drop one and a live product silently reverts to
auto-equalization.

An empty cache has two causes the DB cannot tell apart, which is what made this a bug:

| Cache | Apple | Meaning | Behaviour |
|-------|-------|---------|-----------|
| empty | has prices | never synced — applying would wipe untouched territories | **409 / ToolError**, "Sync IAP prices before applying" |
| empty | no schedule | nothing exists to preserve | **proceed** |
| warm  | — | preserve loop has what it needs | proceed, no ASC call |

`_assert_iap_schedule_replaceable` (`backend/app/api/v1/pricing.py`, shared by the REST route and the
MCP tool) asks Apple once, and only on the empty-cache path. Consulting the DB alone is what produced
the false positive: a freshly created IAP was refused, and the sync it recommended 404'd, so a product
created through the API could never be priced through it.

### No sync needed for a new IAP

The tier ladder is a **global** filesystem cache — `backend/.cache/price_points/{iap,subscription}/{alpha2}.json`,
no app or product in the path — and `compute_price_point_id()` derives any product's price-point ids
from it locally. So `preview` and `apply` work on a brand-new IAP with **no sync of any kind**:

```
pricing_create_iap  →  pricing_preview_iap_prices  →  pricing_apply_iap_prices
```

The `/apps/{id}/iaps/{iap_id}/price-points/sync` URL is misleading: the app and IAP appear only because
Apple's endpoint requires *some* product to quote tiers for, and `fetch_and_cache` strips the product
out before writing. `fetch_and_cache_all(skip_cached=True)` skips territories already on disk, so
re-running it fetches nothing.

`pricing_sync_iap_prices` is the only genuinely per-product sync — it reads *this* product's current
prices, which no sibling can supply.

## Error Signalling

Local guard failures use dedicated exception types in `backend/app/services/asc/errors.py`, never a bare
`ValueError`:

| Exception | Raised by | REST | MCP |
|-----------|-----------|------|-----|
| `ASCRequestInvalidError` | `update_iap` with no fields | 400 | `ToolError` |
| `IAPScheduleUnsyncedError` | `_assert_iap_schedule_replaceable` | 409 | `ToolError` |
| `ChildResourceNotFoundError` | `assert_iap_localization` | 404 | `ToolError` |

The reason is specific: `json.JSONDecodeError` **is** a `ValueError`, and these service methods parse
Apple's JSON. A caller catching `ValueError` around them would turn a malformed upstream body into a
confident 409 whose detail is a raw Python message — both mislabelled and a violation of the
no-raw-errors rule. `test_local_guards_are_not_valueerror_subclasses` is the tripwire.

Relatedly, `_raise_for_asc_error(raw)` is the single place raw ASC responses become `ASCAPIError`. It
tolerates a non-JSON body (an HTML 502 from a gateway in front of ASC) rather than raising
`JSONDecodeError` *past* every `except ASCAPIError`.

## Ownership

Every route and tool goes through the same chain as the rest of the pricing surface —
`_get_verified_app` → `_get_verified_iap`, and `resolve_app` for MCP. The delete-localization path
additionally calls `assert_iap_localization` before deleting by bare child id: without it, a caller who
owns app A could remove a localization belonging to app B. That is the shape of the cross-app IDOR in
[014](014-reviews-module-security-findings.md).

`test_every_route_using_a_membership_guard_maps_it_to_404` asserts that every pricing route calling a
membership guard also translates it, so a fired guard can never escape as a 500.

## MCP Coverage

This work also closed the last gaps between the REST surface and the tool surface (187 tools total —
see [007](007-mcp-integration.md)):

| Tool | Why it matters |
|------|----------------|
| `pricing_create_iap` / `_update_iap` / `_delete_iap` / `_delete_iap_localization` | The lifecycle above |
| `clone_list_operations` / `clone_get_operation` / `clone_retry_operation` | `swap_iap` and `swap_subscription_product` start long multi-step operations; without these an agent starts a swap and goes blind, with no way to read per-step status or resume a partial failure |
| `growth_recommendations` | Exposes the Growth advisor ([012](012-growth-recommendations.md)) |

The two deletes are in `DESTRUCTIVE` (consent-gated); the three status reads are `READ_ONLY` so plan
mode does not prompt. `clone_retry_operation` is deliberately ungated — it resumes an operation the
user already consented to.

## Testing

| File | Covers |
|------|--------|
| `backend/tests/test_iap_lifecycle.py` | Immutability contract, empty-PATCH refusal, `familySharable` spelling, the 404 probe's three branches, IDOR guard, guard-to-404 mapping |
| `backend/tests/test_pricing_fixes.py` | Apply refuses when Apple holds unsynced prices; apply proceeds on a never-priced IAP; preserve loop keeps untouched territories warm-cache |
| `backend/tests/test_consent.py` | The two deletes are gated; the three status reads are annotated read-only |

Verified end to end against the live account: create → sync (0 prices) → preview (204 territories) →
apply 3 → apply 1 with the other 2 preserved → wipe local cache → 409 → sync → apply → delete.
