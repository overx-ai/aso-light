---
id: 011
title: "A rate-limited page silently truncates a price ladder, and the stub is cached forever"
status: fixed
severity: high
created: 2026-10-03
updated: 2026-10-03
source: report
repo: aso-light
files: backend/app/services/asc/pricing.py, backend/tests/test_price_ladder_pagination.py
---

# BUG 011 - A rate-limited page truncates a price ladder, and the stub is cached forever

> **TL;DR** — The price-point pagers `break`ed on any status ≥ 400, so a 429 on page 2 kept page 1 (200
> tiers) as the whole ladder and `PricePointCache` stored it for good; Refresher's Lifetime shipped at €24.49
> in Ireland (target €52.99) and RM 64.90 in Malaysia. Every page now retries through `ASCClient._get` and a
> page that keeps failing raises. Delete truncated cache files once after deploying.

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
`ASCPricingService._price_point_ladder` is the one pager for all three ladders (`get_price_points`,
`get_price_point_equalizations`, `get_iap_price_points`). Every page, the IAP ladder's first page included,
goes through `ASCClient._get` (throttle + 429/5xx/network retries + 401 refresh), so a ladder is either
complete or the fetch raises `ASCAPIError` (`ASCRateLimitError` on a lasting 429) and nothing is cached.
The eight truncated cache files (IAP IE, MY; subscription KE, KG, KH, KN, SI, SK) are deleted so they are
fetched again.

## Regression test
`backend/tests/test_price_ladder_pagination.py`, for the IAP, subscription and equalization ladders:
- a first page answered 429 once is retried;
- a second page answered 429 once is retried, and every tier is returned;
- a second page that keeps failing raises `ASCAPIError` instead of returning page 1.
