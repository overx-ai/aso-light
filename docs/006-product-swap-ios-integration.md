# 006 — Product Swap & iOS Integration

What the iOS app must change when a subscription or IAP `productId` is swapped
in App Store Connect via this tool.

> **In one line:** if the iOS app uses RevenueCat **offerings** (recommended),
> usually nothing. If the iOS app uses RevenueCat **hardcoded productIds** or
> **direct StoreKit**, you must ship a new app version with the new productId.

This doc is the source of truth. The MCP tool `swap.subscription_product`
(and `swap.iap`) returns a *tailored subset* of this guidance computed from
the actual swap outcome.

---

## 1. What a "swap" is

`productId` and `subscriptionPeriod` are immutable in App Store Connect after
a product is created. The only path to change either is:

1. Create a new product with the new `productId` in the **same** subscription
   group (for subscriptions) or under the same app (for IAPs).
2. Copy localizations, price schedule, intro offers, and the review screenshot
   from the old to the new product.
3. Archive the old product so new acquisitions stop while existing
   subscribers keep billing.
4. Re-point any RevenueCat entitlements + offering packages from the old
   `productId` to the new one.

The clone subsystem (`POST /apps/{app_id}/subscriptions/{sub_id}/clone` with
`auto_archive=true`, `swap_revenuecat=true`, or the `swap.subscription_product`
MCP tool) does all four steps in a single operation.

The **subscription group is preserved** by the swap — that's the key invariant
the iOS-side guidance below depends on. Group identity is what RevenueCat's
offerings are pinned to, what Family Sharing eligibility is scoped to, and
what Apple uses to enforce "one intro offer per group lifetime."

---

## 2. What the backend has already done for you

After a successful swap (`status == "done"`):

- New subscription/IAP exists in App Store Connect with the new `productId`,
  in the same subscription group, with localizations / prices / intro offers /
  review screenshot copied from the source.
- Old subscription/IAP is archived. Existing subscribers keep billing on it
  until they cancel or their renewal lapses; new acquisitions are blocked.
- If RevenueCat was configured (`swap_revenuecat=true` and an RC credential
  exists on the app):
  - Every entitlement that referenced the old `productId` now references the
    new one.
  - Every offering package that referenced the old `productId` now references
    the new one. **Offering identifiers and package identifiers are unchanged.**
  - The old RC product is archived.

There is no manual ASC step required — except the existing convention that
**submission for review is not automated** and remains a manual step in the
ASC UI.

---

## 3. Decision tree — does iOS need a code change?

Find the path that matches your iOS app's purchase code. **Most modern apps
are Path 1.** If your app spans multiple paths (e.g. subscriptions via RC,
IAPs via direct StoreKit), apply each path to its respective product type.

### Path 1 — RevenueCat + offerings (recommended)

You read products from the offering, not by hardcoded id:

```swift
// Swift / RevenueCat 5.x
let offerings = try await Purchases.shared.offerings()
guard let current = offerings.current else { return }
for package in current.availablePackages {
    // package.identifier is stable; package.storeProduct.productIdentifier is new after swap
    print(package.storeProduct.productIdentifier)
}
```

✅ **No iOS code change required.**

The package identifier (`$rc_monthly`, `pro_annual`, etc.) is unchanged. Only
the underlying `storeProduct.productIdentifier` changes — and your code
doesn't depend on it.

What to do:
- Confirm the next offerings fetch returns the new `productId` on the same
  package id. RC caches offerings client-side; force a refresh via
  `Purchases.shared.invalidateCustomerInfoCache()` and re-fetch in development
  to verify.
- Sandbox-test a purchase end-to-end with a fresh sandbox tester.

### Path 2 — RevenueCat + hardcoded productIds

You ask RC for products by store identifier directly:

```swift
let products = try await Purchases.shared.products(["com.app.pro_monthly"])
```

❌ **iOS code change + new app release required.**

What to do:
- Replace every hardcoded `productId` string with the new one.
- Ship a new app build through App Store review.
- The old build keeps showing the old product, which is archived: existing
  users can still purchase that product (Apple keeps archived subs purchasable
  for users who already have them in their purchase history) but new users
  cannot. That's typically acceptable, but plan a forced-update if you need
  to cut off the old productId entirely.
- **Strongly consider migrating to Path 1 in this same release.** Path 1
  immunizes you from future swaps.

### Path 3 — Direct StoreKit (no RevenueCat)

You use StoreKit 2 (or StoreKit 1) directly:

```swift
// StoreKit 2
let products = try await Product.products(for: ["com.app.pro_monthly"])
```

❌ **iOS code change + new app release required.**

What to do:
- Replace every hardcoded `productId` in `Product.products(for: [...])` /
  `SKProductsRequest.productIdentifiers`.
- Ship a new app build.
- **Server-side receipt validation** must accept BOTH old and new productIds
  as granting the same entitlement during the transition window — see §4.

---

## 4. The transition window (this is the part that bites)

> Archival ≠ revocation. Both productIds are alive in the wild, possibly for
> months.

Existing subscribers continue billing on the old `productId` until they
cancel or their renewal lapses. For an annual sub with low churn, that can be
a **multi-year tail**. For a monthly sub, expect weeks to months.

Concretely, that means:

- Apple `App Store Server Notifications` / receipt validation will keep
  emitting events with the **old** `productId` for existing subscribers until
  they churn. New subscriptions emit events with the **new** `productId`.
- Your entitlement check must treat old and new `productId` as
  *interchangeable* — both grant "Pro." Add the new id additively. **Do not
  remove the old id** until you can prove no live receipts reference it (a
  good signal: zero `RENEWAL` events for old id over a full billing cycle
  beyond the longest renewal period).

If you use **RevenueCat entitlements**, this is automatic — both productIds
are attached to the same entitlement after the swap, so RC resolves both to
the same `isActive` state on `customerInfo`.

If you **roll your own** receipt validation, change the productId set:

```python
# server-side, BEFORE the swap
PRO_PRODUCT_IDS = {"com.app.pro_monthly", "com.app.pro_annual"}

# AFTER the swap — additive only
PRO_PRODUCT_IDS = {
    "com.app.pro_monthly", "com.app.pro_monthly.v2",
    "com.app.pro_annual",  "com.app.pro_annual.v2",
}
```

Also update analytics / churn dashboards so they don't double-count or miss
either id.

---

## 5. What the swap preserves (so you don't have to re-test it)

Because the new product is in the **same subscription group**, all of these
group-level invariants continue to work without any iOS or backend change:

- **Upgrade / downgrade paths** within the group (e.g. monthly → annual).
- **Family Sharing** eligibility.
- **Free trial / intro-offer eligibility per Apple's rules**: an Apple ID is
  granted *one intro offer per subscription group, lifetime.* If a user
  consumed the intro on the old `productId`, they cannot consume another on
  the new `productId` in the same group. This is automatic; you do not need
  to track it.

---

## 6. Verification checklist (do all of these)

1. **App Store Connect UI**
   - New product is `READY_FOR_REVIEW` (or `APPROVED` once submitted).
   - Old product is `DEVELOPER_REMOVED_FROM_SALE` / archived.
   - Localizations, price schedule, and intro offers on the new product match
     the source. (The MCP `swap.*` tool's response `asc_steps` field shows
     each step's status.)
2. **RevenueCat dashboard** (if RC is wired)
   - Every entitlement that referenced the old product references the new one.
   - Every offering package points at the new product.
   - The old product is archived in RC.
   - The MCP tool's `revenuecat_steps` field confirms each of these.
3. **Sandbox**
   - A fresh sandbox tester can buy through the offering / new productId.
   - The entitlement activates on device.
   - For Path 1, test that the *unchanged* iOS build picks up the new product
     on offerings refresh.
4. **Production analytics** (over the days following the swap)
   - Existing renewal events arrive against the **old** productId (expected).
   - New purchase events arrive against the **new** productId.
   - Total active-subscriber count is conserved across the transition.

---

## 7. Receipt-validation cheatsheet

Minimal server-side example (Python, your backend):

```python
PRO_IDS = {"com.app.pro_monthly", "com.app.pro_annual"}

def grants_pro(product_id: str) -> bool:
    return product_id in PRO_IDS

# After a swap, change ONLY this set, additively:
PRO_IDS = {
    "com.app.pro_monthly", "com.app.pro_monthly.v2",
    "com.app.pro_annual",  "com.app.pro_annual.v2",
}
```

Equivalent on iOS (StoreKit 2, when verifying transactions client-side):

```swift
private static let proIDs: Set<String> = [
    "com.app.pro_monthly", "com.app.pro_monthly.v2",
    "com.app.pro_annual",  "com.app.pro_annual.v2",
]

for await result in Transaction.currentEntitlements {
    guard case .verified(let tx) = result, Self.proIDs.contains(tx.productID) else {
        continue
    }
    // grant Pro
}
```

For RevenueCat, no equivalent change is needed — entitlement names are stable
across the swap.

---

## 8. Rollback

Once `auto_archive=true` has run, the old product is archived. ASC supports
un-archiving but it's disruptive (and the new product is now live). Prefer
**rolling forward**: swap again to a third `productId` if the second one was
wrong. The MCP tool `swap.suggest_new_product_id` produces the next versioned
id (`x.v2 → x.v3`).

If RC step failed (`revenuecat_steps` shows errors) but ASC step succeeded:
fix RC manually in the dashboard (re-attach the new productId to the
entitlements + packages that previously held the old one, archive the old
RC product). The clone operation row supports retry via
`POST /apps/{app_id}/clone-operations/{op_id}/retry` — both ASC and RC
steps are idempotent.

---

## See also

- [005-subscription-management.md](005-subscription-management.md) — the
  underlying subscription/group/intro-offer write paths
- [007-mcp-integration.md](007-mcp-integration.md) — the MCP server, tool
  reference, and PAT lifecycle
- `backend/app/services/asc/clone.py` — `SubscriptionCloner` / `IAPCloner`
- `backend/app/services/revenuecat/swap.py` — `RevenueCatProductSwap`
- `backend/app/mcp/tools/swap.py` — the MCP swap tools and the `_ios_checklist`
  function that produces the tailored subset of this doc at swap time
