---
id: 014
title: "Subscription price sync keeps an arbitrary schedule row per territory and reads only 200 rows"
status: fixed
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py
---

# BUG 014 - Subscription price sync keeps an arbitrary schedule row per territory and reads only 200 rows

> **TL;DR** — The subscription price sync read one page (200 rows) and kept whichever schedule row came last
> for each territory. Once dated changes existed (bug 013), a territory could read as its old price or as a
> price not yet in effect. It now follows every page and keeps the row in effect today, by Apple's Pacific
> calendar.

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
- `get_subscription_prices` follows `links.next` through every page (`_all_pages`, now shared with
  `_price_point_ladder`). A later page that still fails after the client's retries raises rather than
  returning a partial schedule, as bug 011 already does for the price ladder.
- Per territory it returns one row: the latest `startDate` on or before today on Apple's Pacific day.
  Undated rows (the initial price) sort before any dated one, and rows dated after today are dropped.
- A territory whose only row starts in the future is returned as unpriced.

## Regression test
`backend/tests/test_subscription_price_sync_schedule.py`
