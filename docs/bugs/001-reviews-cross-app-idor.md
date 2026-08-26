---
id: 001
title: "Reviews module: cross-app IDOR on review_id/response_id"
status: open
severity: critical
created: 2026-08-26
updated: 2026-08-26
source: audit
repo: aso-light
files: backend/app/api/v1/reviews.py, backend/app/mcp/tools/reviews.py, backend/app/services/asc/reviews.py
---

# BUG 001 - Reviews module: cross-app IDOR on review_id/response_id

## Symptom

ASC scopes `/v1/customerReviews/*` and `/v1/customerReviewResponses/*` to the whole Apple
**team**, not per-app. `list_reviews` correctly scopes by app (`GET /v1/apps/{asc_app_id}/customerReviews`),
but every other entry point takes a bare `review_id` / `response_id` and never verifies it belongs to
the app the caller was verified against:

- `get_review`, `draft`, `translate` (`app/api/v1/reviews.py:178,203,255` + MCP mirrors) call
  `GET /v1/customerReviews/{review_id}` with no app check.
- `create_response` posts a `review` relationship by bare `review_id` with no app check.
- `update_response` / `delete_response` act on bare `response_id` with no app check — the PATCH/DELETE
  routes even discard the `review_id` path param (`# noqa: ARG001`).

**Repro (expected → actual):** User owns app A (ASO-Light) and app B (ASO-Light), both apps under the
same Apple team / same ASC credential (e.g. an agency managing multiple client apps on one team — the
ASO-Light tenants are distinct, the underlying Apple team is shared). Expected: a request scoped to app A
cannot touch app B's review/response IDs, 404. Actual: passing app B's `review_id`/`response_id` while
authenticated for app A succeeds — the caller can read, AI-draft against, translate, respond to, edit, or
delete another app's reviews.

Full analysis: `docs/014-reviews-module-security-findings.md` (finding C1).

## Root cause

`get_review` / `create_response` / `update_response` / `delete_response` never prove the review's parent
app equals `app.asc_app_id` before acting — they only verify the caller owns *some* app (`_get_verified_app`),
not that the specific review/response belongs to *that* app.

## Fix

Per docs/014's documented fallback (chosen over the "confirm ASC exposes reverse app linkage" path, which
needs live-tenant verification we don't have): persist a lightweight `review_id → app_id` map populated by
`list_reviews` (reviews are fetched live today, never stored). Add a shared helper —
`assert_review_belongs_to_app(session, review_id, asc_app_id)` — that checks the map and is called before
any read/draft/translate/mutate on a specific review or response. For `update_response`/`delete_response`,
resolve `response_id → review_id → app_id` via the same map (stop discarding the route's `review_id`; verify
the response's review == the path's `review_id` **and** that review's app == `asc_app_id`). Return **404**
(not 403) on any mismatch so existence isn't leaked. {filled in during implementation}

## Regression test

{the test that fails before the fix and passes after — this IS the TDD step, filled in during implementation}
