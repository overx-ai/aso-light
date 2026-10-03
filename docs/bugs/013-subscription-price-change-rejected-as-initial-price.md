---
id: 013
title: "Every price change to an approved subscription is rejected as a second initial price"
status: fixed
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py, backend/app/api/v1/pricing.py, backend/app/mcp/tools/pricing.py
---

# BUG 013 - Every price change to an approved subscription is rejected as a second initial price

> **TL;DR** — Both subscription apply paths POSTed `/subscriptionPrices` with no `startDate`, and Apple reads an
> undated price as the initial one. So once a subscription was approved, every change failed with "Initial
> price cannot be created again". A change to an already-priced territory is now dated on the next US Pacific
> day, so it takes effect tomorrow, not immediately.

## Symptom
- `pricing_apply_subscription_prices` (app 3, `refresher.monthly.v3`, 17 territories) returned
  `applied: 0, failed: 17`.
- Every territory failed with `Initial price cannot be created again after subscription is approved.`

## Root cause
- `ASCPricingService.create_subscription_price` sends only `preserveCurrentPrice`.
- Apple's subscription price schedule has one undated baseline, the initial price. Every later change is
  dated: it needs a `startDate` that is a future date on Apple's pricing calendar, which runs on US Pacific
  time.
- Other App Store Connect API tools report the same rule: rorkai/App-Store-Connect-CLI#2845 and
  akoskomuves/appstoreconnect-mcp#60.

## Fix
- `next_price_change_date(now)` returns the next US-Pacific day — Apple's pricing calendar. A UTC date
  was refused as a future `startDate` where the Pacific one was accepted (rorkai/App-Store-Connect-CLI#2845).
- `create_subscription_price(start_date=)` sends `attributes.startDate` when given and stays undated
  otherwise, so a territory's opening price is still created as the initial price
  (akoskomuves/appstoreconnect-mcp#60: omit `startDate` for the opening price, supply it for every change).
- Both apply paths (REST `apply_subscription_prices`, MCP `pricing_apply_subscription_prices`) pass
  `subscription_price_start_date(current_price)`: dated when the territory has a cached price, undated when
  it has none. "Already priced" is read from the local cache, so re-sync prices before applying — a stale
  empty cache sends an undated change and Apple refuses it with this bug's error rather than mispricing.
- A dated change is scheduled, not immediate: the new price takes effect on that Pacific day.

## Regression test
`backend/tests/test_subscription_price_start_date.py`
