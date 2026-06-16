# 014 — Review Responses Module: Security & Quality Findings

> **Status:** Findings only — **no code changed.** Produced by a `/code` review pass
> on 2026-06-16. The Reviews module fixes were intentionally paused pending a
> decision on **C1** (see *Open question* below).
>
> **Scope reviewed:** `app/api/v1/reviews.py`, `app/mcp/tools/reviews.py`,
> `app/services/asc/reviews.py`, `app/services/reviews/serialize.py`,
> `app/services/reviews/draft.py`, `app/services/metadata/translate.py`,
> `frontend/src/pages/ReviewsPage.tsx`, `frontend/src/components/reviews/ReviewDrawer.tsx`,
> `frontend/src/lib/hooks.ts` (reviews hooks).

## Summary

**4 Critical · 6 Important · 5 Minor.** Functionally solid and the
**suggestion-only contract is honored** (no path auto-posts AI output), there is
**no XSS** (all review/AI text renders as escaped React nodes), and **app-level
ownership** is enforced on every entry point via `_get_verified_app` / `resolve_app`.

The real risks are: a **systemic cross-app IDOR** on the review/response
sub-resources, and the **AI draft path completely bypassing the 500-call/month
cap**. There are **no tests** for this module today (only `test_preview_*` match
"review" by substring).

---

## Critical

### C1 — Cross-app IDOR: `review_id` / `response_id` never scoped to the verified app
**Where:** `app/api/v1/reviews.py` (`get_review` :178, `draft` :203, `translate` :255, `create_response` :326, `update_response` :355, `delete_response` :384) and the mirror MCP tools `app/mcp/tools/reviews.py:181-398`. Root cause in `app/services/asc/reviews.py`:
- `list_reviews` is correctly **app-scoped**: `GET /v1/apps/{asc_app_id}/customerReviews` (:64).
- `get_review` (:68) → `GET /v1/customerReviews/{review_id}` — **team-scoped, no app link.**
- `create_response` (:87) posts a `review` relationship by bare `review_id` — **no app link.**
- `update_response` (:107) / `delete_response` (:123) act on bare `response_id` — **no app link.** The PATCH/DELETE routes even discard the `review_id` path param (`# noqa: ARG001`).

**Impact:** ASC scopes `/v1/customerReviews/*` and `/v1/customerReviewResponses/*` to the whole **Apple team**. A user who owns app A in ASO-Light (under credential X) can pass a `review_id`/`response_id` belonging to app B — also under credential X (e.g. an agency managing multiple client apps on one Apple team, where the ASO-Light tenants are distinct) — and **read, draft, translate, respond to, edit, or delete** app B's reviews. Same vulnerability class as the ASA cross-tenant leak (fixed in `a3b795b`), here at the sub-resource level.

**Recommended fix:** before any read/draft/translate/mutate, prove the review's
parent app == `app.asc_app_id`. Add a shared helper
`assert_review_belongs_to_app(svc, review_id, asc_app_id)` and call it in
`get_review`/`draft`/`translate`/`create_response`; for `update_response` /
`delete_response`, resolve `response_id → review → app` (stop discarding the
route's `review_id`; verify the response's review == `review_id` **and** that
review's app == `asc_app_id`). Return **404** (not 403) on mismatch so existence
isn't leaked.

> ### ⚠ Open question (blocks the C1 fix mechanism)
> The clean fix assumes `GET /v1/customerReviews/{id}` can return the **parent-app
> linkage** (`include=app` / a `relationships.app`). Apple documents the
> relationship one-directionally — app → reviews
> (`GET /v1/apps/{id}/relationships/customerReviews`) — and it is **not confirmed**
> that the review resource exposes the reverse `app` linkage. **This must be
> verified against a live ASC tenant** before the fix is trusted, because a
> naive `assert parent == asc_app_id` would *fail closed* (reject everything) and
> break the feature if the linkage isn't returned.
>
> **Fallback if no reverse linkage exists:** persist a lightweight
> `review_id → app_id` map when `list_reviews` runs (reviews are currently fetched
> live and not stored), and verify mutations against that map; or re-list the
> app's reviews and check membership (more expensive, paginated).

### C2 — AI draft path bypasses the 500-call/month cap entirely
**Where:** `app/api/v1/reviews.py:234-242`, `app/mcp/tools/reviews.py:236-244`.
`draft_review_reply` calls `draft_reply(...)` directly — it never goes through
`translate_with_cache`, so there is **no cap check, no counter, nothing recorded
in `MetadataTranslationCache`**. Every draft is an uncapped, uncached Anthropic
call; an MCP loop can issue unlimited paid drafts (spend-DoS + contract violation
— CLAUDE.md promises a per-app 500/mo soft cap).
**Fix:** extract the cap check/record from `translate_with_cache` into a reusable
`enforce_and_record_ai_call(session, app_id, kind="draft")` (a usage counter
scoped to `app_id` — drafts are non-deterministic so they need a counter, not the
content cache) and call it before `draft_reply(...)`, raising
`TranslationQuotaExceededError → HTTP 429` / `ToolError`.

### C3 — `translate` swallows the quota signal into a generic 502 (never 429)
**Where:** `app/api/v1/reviews.py:307-316`, `app/mcp/tools/reviews.py:294-310`.
Unlike `metadata.py:652-661` (which maps `TranslationQuotaExceededError → 429`
and `TranslatorUnavailableError → 502`), the reviews translate handler wraps the
call in a blanket `except Exception → 502 "AI translation service unavailable."`
So when the cap is hit, the user is told the service is down and the cap message
is lost — the graceful-at-cap behavior is broken.
**Fix:** catch `TranslationQuotaExceededError → 429` and `TranslatorUnavailableError
→ 502` *before* the broad fallback, in both REST and MCP (import both from
`app.services.metadata.translate`).

### C4 — Translation-cache cross-feature bleed via reused `field_kind`
**Where:** `app/api/v1/reviews.py:305` (`field_kind="description"  # type: ignore`),
mirror in the MCP tool; model `app/models/metadata.py:124-163`,
`app/services/metadata/translate.py:377-387`.
Review translate reuses `field_kind="description"`, so a review body identical to
a previously-translated metadata description returns the cached metadata
translation (and vice-versa) — content bleed across features within an app.
Combined with C1, app-B review text could be persisted under app-A's cache
namespace.
**Fix:** add a dedicated `"review_body"` to `FieldKind` / `FIELD_CHAR_LIMITS` and
use it for review translations (drop the `description` reuse + `type: ignore`).
The `app_id` cache key is your own unique PK, so there is **no direct
cross-tenant** read — the leak axis is the *content source*, not the app id.

---

## Important

- **I1 — `has_response` filter corrupts pagination.** `reviews.py:170-175` /
  MCP `:173-178`: Apple has no `has_response` filter, so the route fetches one
  page, filters in memory, but still returns the *unfiltered* `next_cursor`. A
  "Needs reply" view can return 3 of 100 with a non-null cursor; the frontend
  hard-codes `limit:100` and never paginates. **Fix:** loop pages server-side
  until `limit` post-filter rows (bounded), or suppress `next_cursor` when the
  filter is active.
- **I2 — Cap check-then-increment race + `flush` vs `commit`.**
  `translate.py:389-423`: two concurrent translates for one app each see
  `count==499` and both proceed → 501. The new row is `flush`ed but committed by
  the caller, so a rollback on the broad `except` can under-count real spend.
  **Fix:** serialize via a per-app usage row (`FOR UPDATE`) or insert-as-
  serialization-point inside one transaction.
- **I3 — update/delete don't distinguish not-found/state from 502.**
  `reviews.py:355-401`, `services/asc/reviews.py:107-125`: a PATCH/DELETE on an
  already-deleted or non-editable-state response surfaces as a blanket 502.
  **Fix:** inspect `ASCAPIError.status_code` → 404/409 with clean messages, keep
  5xx → 502 (REST `_asc_to_502` and MCP `_wrap_asc`).
- **I4 — Massive REST↔MCP duplication; canonical `serialize.py` is unused.**
  `reviews.py:53-130` vs MCP `:39-120` ship private copies of `_serialize_review`
  / `_extract_cursor` / `_territory_to_locale`, while `services/reviews/serialize.py`
  already has canonical versions neither imports — **and they've already drifted**
  (the route copies compute `theme`, the shared one doesn't). **Fix:** make
  `serialize.py` the single source (add `theme`), import it in both surfaces,
  delete the local copies; extract the read+ownership+serialize core into one
  service function.
- **I5 — locale-guess passthrough returns foreign reviews untranslated.**
  `reviews.py:291-294` / MCP `:290-292`: `source_locale` is derived from
  *territory*, not the review's language, so a Spanish review from a US user (or
  any territory→locale == target) is returned raw with `cached=True`. **Fix:**
  drop the territory-equality shortcut or gate it behind a known-source signal;
  normalize `target_locale` before comparing.
- **I6 — Frontend double-submit window.** `hooks.ts:2722-2766, 2842/2880/2912` +
  `ReviewDrawer.tsx`: after a successful `createReply`, the drawer's `dirty`
  check can re-enable "Post reply" before invalidation lands → double create →
  Apple 409. **Fix:** guard re-submit on `isPending`/`isSuccess` or switch
  create→update once a response id is known via `onSuccess` cache write.

## Minor

- **M1** — `draft`/`translate` each do a full `get_review` round-trip with no reuse (`reviews.py:220-223, 279-282`).
- **M2** — `_extract_cursor` naive string-split on Apple's `next` URL; use `urllib.parse` (`reviews.py:122-130`, `serialize.py:95-104`).
- **M3** — `int(rating or 0)` yields out-of-domain rating 0 on malformed payloads (`reviews.py:89`, `serialize.py:85`).
- **M4** — Response-body length validated in two places (`mcp/tools/reviews.py:320-324` vs `schemas/review.py:34-35`); centralize on `RESPONSE_BODY_MAX_LEN`.
- **M5** — Frontend reads translate target locale from the unrelated `localStorage["metadata-source-locale"]` key (`ReviewDrawer.tsx:42,97-100`); give reviews their own preference key.

## Clean / not an issue

- **Suggestion-only contract honored** — no draft/translate path calls a write; posting always requires a separate explicit `respond`/`update_response`.
- **No XSS** — no `dangerouslySetInnerHTML`; review title/body + AI output render as escaped `<Text>` nodes.
- **Raw-error leakage mostly clean** — `_asc_to_502` / MCP `_wrap_asc` genericize ASC + Anthropic errors (the one gap is C3 mis-mapping the quota error).
- **App-level ownership** enforced on every entry point. The gap (C1) is the review/response *sub-resource*, not the app.

## Highest-value tests to add (none exist today)

1. **IDOR (C1):** two apps under the same credential — assert app-A's owner gets 404 on GET/POST/PATCH/DELETE with app-B's `review_id`/`response_id`.
2. **Cap on draft (C2):** with 500 usage rows in the window, assert `draft` → 429.
3. **Cap signal on translate (C3):** force `TranslationQuotaExceededError` → assert REST 429 / MCP `ToolError` with the quota message.
4. **Suggestion-only:** assert `draft`/`translate` make zero ASC write calls.
5. **Response lifecycle (I3):** mock ASC 404 on update/delete → assert clean 404.
6. **Cache namespace (C4):** translate a review body, then a metadata `description` with identical text → assert no collision once `field_kind="review_body"` lands.

## Suggested fix order (when un-paused)

1. **C1** once the ASC review→app linkage is confirmed (or the fallback chosen) — highest risk.
2. **C2 + C3** (AI spend control + correct cap signal) — clear-cut, ship together.
3. **C4** (`review_body` field_kind) — clear-cut, additive.
4. **I1–I6**, then **M1–M5**, with the DRY `serialize.py` consolidation (I4) folded in.
5. Add the six tests above alongside the fixes.
