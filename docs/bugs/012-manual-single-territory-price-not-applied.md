---
id: 012
title: "Pricing UI cannot set one territory's price: pinned manual prices are never applied"
status: fixed
severity: medium
created: 2026-10-03
updated: 2026-10-03
source: manual
repo: aso-light
files: frontend/src/pages/PricingPage.tsx, frontend/src/components/pricing/PriceGrid.tsx, frontend/src/components/pricing/PriceMultiplierPanel.tsx
---

# BUG 012 - A pinned territory price is not editable without a preview and never reaches Apply

> **TL;DR** — The pin icon on a pricing-grid row is meant to set one territory's price by hand. Without a
> preview the input never appeared. With a preview, Apply still sent only the raw preview items, so the
> pinned price was dropped. In the subscription default "none" mode, Apply sent `[]`. The backend already
> handled a one-item apply, so the fix is frontend-only.

## Symptom

Pricing → Subscriptions or IAPs → pin the DE row:

1. Without a preview: nothing becomes editable. `PriceGrid` rendered the "Suggested Price" column (which
   holds the input) only when `previewItems` was non-empty, and with no resolved manual item yet,
   `mergedPreviewItems` was `null`.
2. Preview → pin DE → type a price → Apply: the request carries the computed DE price, not the typed one.
   `PriceMultiplierPanel` got the raw `preview`, not the merged `mergedPreviewItems`.
3. Subscriptions, index "none": Apply sends `items: []`, because `handleApply` short-circuited on `isNoChanges`.

## Fix

- `PriceGrid`: the price column also shows while any territory is pinned.
- `PricingPage`: both tabs pass `toPanelPreview(mergedPreviewItems, …)` to the panel, so pinned prices are
  part of what Apply sends. Manual state is cleared after a successful apply.
- `PriceMultiplierPanel.handleApply`: sends the preview items whenever there is a preview, including in
  "none" mode, where the preview holds only the pinned rows.

The ±50% safety band still applies server-side; use the per-row force toggle to bypass it.
