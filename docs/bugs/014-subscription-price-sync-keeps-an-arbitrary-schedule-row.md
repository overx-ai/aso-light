---
id: 014
title: "Subscription price sync keeps an arbitrary schedule row per territory and reads only 200 rows"
status: open
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py
---

# BUG 014 - Subscription price sync keeps an arbitrary schedule row per territory and reads only 200 rows

> **TL;DR** — `get_subscription_prices` reads one page of `/subscriptions/{id}/prices` (200 rows) and
> returns every schedule row. The sync then keeps whichever row came last for each territory. Once a dated
> change exists (bug 013), a territory reads as either its old price or a price that hasn't started yet, and
> the territories past row 200 read as unpriced.

## Symptom
- After the 2026-10-03 alignment, Refresher monthly has 192 price rows and yearly has 194. Each changed
  territory has two rows: the undated price that existing subscribers keep, and the new price dated
  2026-10-04.
- A sync stores either of those two as the territory's "current" price. That value drives three things: the
  ±50% safety band, `preserve_current_price_on_increase`, and the dated/undated choice from bug 013.
- Once a listing passes 200 rows, every territory after the cut-off disappears from the cache.

## Root cause
- One request with `limit: 200`, and `links.next` is never followed.
- `startDate` is never read, so a scheduled row and a superseded row look the same as the current one.

## Fix
(filled in with the fix)

## Regression test
`backend/tests/test_subscription_price_sync_schedule.py`
