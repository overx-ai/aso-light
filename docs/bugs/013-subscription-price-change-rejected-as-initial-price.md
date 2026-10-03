---
id: 013
title: "Every price change to an approved subscription is rejected as a second initial price"
status: open
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py, backend/app/api/v1/pricing.py, backend/app/mcp/tools/pricing.py
---

# BUG 013 - Every price change to an approved subscription is rejected as a second initial price

> **TL;DR** — Both subscription apply paths POST `/subscriptionPrices` with no `startDate`. Apple reads an
> undated price as the subscription's initial price, so once the subscription is approved every territory
> fails with "Initial price cannot be created again after subscription is approved". Subscription prices
> could never be changed from aso-light after approval.

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
(filled in with the fix)

## Regression test
`backend/tests/test_subscription_price_start_date.py`
