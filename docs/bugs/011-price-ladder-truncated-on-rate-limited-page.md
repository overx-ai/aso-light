---
id: 011
title: "A rate-limited page silently truncates a price ladder, and the stub is cached forever"
status: open
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py, backend/tests/test_price_ladder_pagination.py
---

# BUG 011 - A rate-limited page truncates a price ladder, and the stub is cached forever

> **TL;DR** — `get_iap_price_points` and `get_price_points` page through Apple's price points with a bare
> `http.get` loop that `break`s on any status ≥ 400. A 429 on page 2 kept page 1 (200 tiers) as if it were
> the whole ladder, and `PricePointCache` stored it for good. Resolving a price above the 200th tier then
> snapped to the highest visible one: Refresher's Lifetime shipped at €24.49 in Ireland (target €52.99) and
> RM 64.90 in Malaysia.

## Symptom
- `pricing_resolve_iap_price(IE, 52.99)` on Refresher's Lifetime IAP answers €24.49.
- The cached ladder `backend/.cache/price_points/iap/IE.json` holds exactly 200 tiers; DE holds 801.
- The cache held six ladders cut at a page boundary: IAP IE and MY at 200; subscription KH, KN, SI and SK
  at 200, plus one at 400 and one at 600.

## Root cause
Both methods paginate by hand, because they need `included` (the territory currency), with
`raw = await http.get(next_url); if raw.status_code >= 400: break`. A later page skips the client's rate
limiter, retry and backoff (`ASCClient._send`). The first 429 ends the loop, and the caller cannot tell a
short ladder from a full one.

## Fix
Later pages go through `ASCClient._send` (throttle + 429/5xx/network retries + 401 refresh) and
`_raise_for_asc_error`, so a ladder is either complete or the fetch raises and nothing is cached. The six
truncated cache files are deleted so they are fetched again.

## Regression test
`backend/tests/test_price_ladder_pagination.py`:
- a second page answered 429 once is retried, and every tier is returned;
- a second page that keeps failing raises `ASCAPIError` instead of returning page 1.

Both run for the IAP and subscription ladders.
