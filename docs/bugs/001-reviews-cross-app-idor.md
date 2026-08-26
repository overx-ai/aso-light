---
id: 001
title: "Reviews module: cross-app IDOR on review_id/response_id"
status: fixed
severity: critical
created: 2026-08-26
updated: 2026-08-26
source: audit
repo: aso-light
files: backend/app/api/v1/reviews.py, backend/app/mcp/tools/reviews.py, backend/app/services/asc/reviews.py, backend/app/services/reviews/ownership.py, backend/app/models/review_app_map.py
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
needs live-tenant verification we don't have): persist a lightweight `review_id → app_id` map (table
`review_app_map`) plus a `response_id → review_id` map (table `review_response_map`), both defined in
`app/models/review_app_map.py`. Both are populated by `list_reviews` (which always requests
`include=response`, so a page's reviews and their response ids are known together) and, defensively, by
`get_review`/`draft`/`translate` (their single-review ASC payload is the same shape, wrapped in a
one-element list) and by `create_response` (records its own newly-created `response_id → review_id` pair
immediately, since `list_reviews` wouldn't otherwise learn about it until its next page fetch). Population
logic lives in `app/services/reviews/ownership.py:record_review_app_mappings` /
`record_response_mapping` — a single bulk SELECT + per-row upsert (no dialect-specific `ON CONFLICT`,
mirroring `app.services.reviews.themes.upsert_classifications`'s SQLite/Postgres portability tradeoff).

The guard itself: `app/services/reviews/ownership.py:assert_review_belongs_to_app(session, review_id,
app_id)` checks the map and 404s (via the existing `ChildResourceNotFoundError`, reused rather than
duplicated — its docstring in `app/services/asc/errors.py` now covers both the subscription-child and
reviews cases) unless `review_id` is mapped to exactly `app_id`. `assert_response_belongs_to_app(session,
response_id, app_id, review_id=...)` resolves `response_id → review_id` first (also 404 if unseen), then —
when a path `review_id` is supplied (REST only; MCP's `update_response`/`delete_response` never took one) —
asserts it matches the resolved review_id (this is the "stop discarding the route's `review_id`; actually
use it" fix), then delegates to `assert_review_belongs_to_app`. Both REST (`app/api/v1/reviews.py`) and MCP
(`app/mcp/tools/reviews.py`) call these identically via thin per-module wrappers
(`_assert_review_owned`/`_assert_response_owned`) that translate `ChildResourceNotFoundError` into
`HTTPException(404, ...)` (REST) or `ToolError(...)` (MCP) — matching the existing convention pricing.py
already uses for its own child-membership guard. The assertion runs *before* an `ASCClient` is built, so a
404 from our own DB map doesn't pay for a credential decrypt + client construction it doesn't need.

Fail-closed: a review/response never observed by `list_reviews` for ANY app — whether it belongs to a
different app on the same Apple team, or is a stale/unknown id — 404s identically, so existence is never
leaked. This means a review reached via `get_review`/`draft`/`translate`/MCP without a prior `list_reviews`
call for its own app (e.g. a pre-fix id, or a caller that skips the list step) also 404s; the normal UI/MCP
flow always lists before acting on a specific review, so this is accepted as intended behavior, not a
regression.

Migration: `backend/alembic/versions/1402f6657400_add_review_app_map.py` (creates `review_app_map`,
`review_response_map`; head was `5f914bb9c418`).

## Regression test

`backend/tests/test_review_app_map.py` — two independent ASO-Light tenants (app A / app B, each its own
user + credential) share one fake ASC client that answers a bare review/response id lookup regardless of
which app's client asked (mirroring real, team-scoped ASC behavior). `list_reviews` is only ever called for
app A. Twelve `test_{rest,mcp}_{get_review,draft,translate,create_reply,update_reply,delete_reply}_cross_app_*`
tests each attempt one of the six vulnerable entry points against app B's `rev-B1`/`resp-B1` while
authenticated for app A and assert `HTTPException(404)` (REST) / `ToolError` (MCP). Confirmed these all
failed with "DID NOT RAISE" against the pre-fix tree — proving the vulnerability, not just a helper's
existence — before implementing the fix. `test_{rest,mcp}_same_app_access_still_works` are the positive
regression guard: the same six entry points against app A's own `rev-A1`/`resp-A1` continue to succeed
unchanged. The full pre-existing `backend/tests/test_reviews.py` suite (29 tests) and the full backend
suite (397 tests total) stay green.
