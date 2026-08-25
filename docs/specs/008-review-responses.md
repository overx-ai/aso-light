---
id: 008
title: "Review responses — read, AI-suggest, translate, post"
status: done
created: 2026-05-06
updated: 2026-08-26
tasks: []
---

# 008 - Review Responses

## Problem

Developers need to triage and reply to App Store reviews across territories and languages. Today they juggle ASC web → translator → ASC web. We want a single pane: list reviews, filter, click → see full text, get an AI-drafted reply in the reviewer's language, edit, post — all without leaving aso-light.

This spec covers feature **#37 (Review Responses)** from the aso.dev parity research.

## Scope

In:
- ASC Customer Reviews list (paginated) with filters: territory, rating (1–5), has-response.
- View a single review's full text + existing response (if any).
- AI-drafted reply via Claude Haiku 4.5 — uses review locale, takes optional tone hint, never auto-posts.
- Translate review body to the operator's preferred locale (re-uses existing `translate_with_cache`).
- Create / update / delete a `customerReviewResponse` against ASC.

Out (deferred):
- DB caching of reviews (ASC remains source of truth; live fetch with TanStack Query stale-time).
- Bulk reply / templates.
- Sentiment analytics, charts.
- Filing complaints with Apple about abusive reviews.
- Notifications / push when new low-star reviews land.

## ASC API surface used

- `GET /v1/apps/{id}/customerReviews?include=response&filter[rating]=&filter[territory]=&sort=-createdDate&limit=200`
- `GET /v1/customerReviews/{id}?include=response`
- `POST /v1/customerReviewResponses` — body: `{ data: { type, attributes: { responseBody }, relationships: { review: { data: { type, id } } } } }`
- `PATCH /v1/customerReviewResponses/{id}` — body: `{ attributes: { responseBody } }`
- `DELETE /v1/customerReviewResponses/{id}`

Reply char-limit: **5970** (Apple's documented max for `responseBody`).

## Architecture

**Backend service** — `backend/app/services/asc/reviews.py`
`ASCReviewService` (parallels `ASCMetadataService` patterns):
- `list_reviews(app_id, *, territory=None, rating=None, has_response=None, page_cursor=None, limit=50)` → paginated list, includes `response` relationship.
- `get_review(review_id)` → single review with response.
- `create_response(review_id, body)` → POST.
- `update_response(response_id, body)` → PATCH.
- `delete_response(response_id)`.

Reuses `ASCClient.from_credential(...)` and the existing rate-limited `_get(...)` / `_post(...)` helpers.

**AI helper** — `backend/app/services/reviews/draft.py`
`draft_reply(translator, *, review_body, review_rating, review_locale, tone="neutral")` → Claude Haiku call returning a reply string in the same locale as the review. Reuses `AnthropicTranslator` infrastructure but uses a different system prompt focused on customer-support tone.

Tones: `neutral` (default), `apologetic`, `appreciative`. Each prepends 2-3 lines of instruction to the prompt.

**Schemas** — `backend/app/schemas/review.py`
- `ReviewOut`: id, asc_review_id, rating, title, body, territory, reviewer_nickname, created_date, edited, response (optional `ReviewResponseOut`).
- `ReviewResponseOut`: id, body, last_modified_date, state.
- `ReviewListOut`: items + pagination cursor.
- `ReplyIn`: body (1–5970 chars).
- `DraftIn`: tone (Literal[neutral, apologetic, appreciative]).
- `DraftOut`: suggestion, locale.

**API** — `backend/app/api/v1/reviews.py`, mounted under `/apps`:
- `GET /apps/{app_id}/reviews?territory=&rating=&has_response=&cursor=` → `ReviewListOut`
- `GET /apps/{app_id}/reviews/{review_id}` → `ReviewOut`
- `POST /apps/{app_id}/reviews/{review_id}/draft` body `DraftIn` → `DraftOut` (no DB write; suggestion only)
- `POST /apps/{app_id}/reviews/{review_id}/translate` body `{target_locale}` → `{translation, cached}` (re-uses `translate_with_cache` against the review body, treating it as a free-form text field with `field_kind="review_body"`)
- `POST /apps/{app_id}/reviews/{review_id}/respond` body `ReplyIn` → `ReviewResponseOut`
- `PATCH /apps/{app_id}/reviews/{review_id}/respond/{response_id}` body `ReplyIn` → `ReviewResponseOut`
- `DELETE /apps/{app_id}/reviews/{review_id}/respond/{response_id}` → 204

The mutate/delete paths carry `{response_id}` explicitly (deviation from this
spec's original draft, which keyed them off the review alone). `GET
/reviews/{id}` already returns `response.id`, so passing it in the URL avoids an
extra ASC round-trip to resolve "the" response for a review.

All endpoints verify ownership via the existing `_get_verified_app(app_id, user_id, session)` helper.

**Frontend** — `frontend/src/pages/ReviewsPage.tsx` + `frontend/src/components/reviews/`
- Routed at `/apps/:id/reviews`, added to per-app sub-nav (`AppNavItem`).
- Top filter bar: territory `Select` (BCP-47 set), rating `Select` (1–5 / any), `Switch` "needs reply".
- DataTable: rating stars, territory chip, reviewer, title preview, body preview (lineClamp 2), created date, response badge (✓ replied / —).
- Row click → side `Drawer`:
  - Full review (rating, title, body), reviewer + territory + date.
  - "Translate to my locale" button — calls translate endpoint, shows the translation under the original.
  - "Suggest reply" — tone picker (3 options), button → AI draft fills the reply textarea.
  - Reply textarea (5970 char counter), Save / Update / Delete buttons depending on existing response state.
- Hooks in `lib/hooks.ts`: `useReviews`, `useReview`, `useDraftReply`, `useTranslateReview`, `useCreateReply`, `useUpdateReply`, `useDeleteReply`.

## Edge cases

- **Reviews without bodies** — Apple sometimes returns rating-only entries. Show "(no review text)" in the list; AI draft / translate disabled.
- **Already-responded** — the existing reply is editable (PATCH) or removable (DELETE). UI shows the existing reply and toggles between Save/Update.
- **Rate limits** — ASC throttles the customer-reviews endpoint heavily. Use the existing rate-limiter + 429 backoff in `ASCClient`. Frontend stale-time 60s.
- **No `ANTHROPIC_API_KEY`** — `/draft` returns 503; UI hides the Suggest button with a tooltip.
- **Char limit 5970** — schema `Field(max_length=5970)` + frontend live counter.
- **Locale of review** — Apple returns territory but not language; we infer locale by territory's primary language (reuse `app/data/territories.py` mapping).
- **Pagination cursor decoding** — `_extract_cursor()` (in `api/v1/reviews.py`, mirrored in `mcp/tools/reviews.py`) parses Apple's `links.next` via `urllib.parse.parse_qs` rather than a hand-rolled string split, so a percent-encoded cursor token is decoded exactly once before being re-sent as the next request's `cursor` param — a hand split would leave it encoded and httpx would double-encode it on resend. Fixed + regression-tested (`test_extract_cursor_decodes_percent_encoded_token`) in the T8 QA pass.

## Verification

1. `make dev`, login, pick an app with reviews, navigate to the new Reviews tab.
2. Filter by rating=1 territory=US — list updates; verify the network call carries the filter params.
3. Click a review → drawer opens with full body. Click **Translate** — translation appears, cache hit on second click.
4. Click **Suggest reply** → reply textarea fills; tone "apologetic" produces a noticeably softer draft.
5. Edit, click **Post reply** → ASC accepts (201); reply badge in the list flips to ✓.
6. Click the same review again → **Update** mutates, **Delete** removes.
7. Negative path: unset `ANTHROPIC_API_KEY`, restart — Suggest hidden, translate works.

## Critical files (new + edit)

- New `backend/app/services/asc/reviews.py`
- New `backend/app/services/reviews/draft.py`
- New `backend/app/schemas/review.py`
- New `backend/app/api/v1/reviews.py` + register in `backend/app/api/v1/__init__.py`
- New `frontend/src/pages/ReviewsPage.tsx`
- New `frontend/src/components/reviews/ReviewDrawer.tsx`
- Edit `frontend/src/lib/hooks.ts` — add 7 review hooks
- Edit `frontend/src/types/index.ts` — add Review types
- Edit `frontend/src/App.tsx` — route
- Edit `frontend/src/components/AppNavItem.tsx` — sub-nav link

## Tasks

| ID | Description | Files |
|----|-------------|-------|
| T1 | `ASCReviewService` (list/get reviews, create/update/delete response) | `services/asc/reviews.py` |
| T2 | AI draft helper (`draft_reply`, tone-aware system prompt) | `services/reviews/draft.py` |
| T3 | Review-theme classifier + per-theme reply templates (feeds the AI draft prompt; beyond the spec's literal text, already shipped) | `services/reviews/templates.py` |
| T4 | Schemas: `ReviewOut`, `ReviewResponseOut`, `ReviewListOut`, `ReplyIn` (1–5970 chars), `DraftIn`, `DraftOut`, `TranslateReviewIn`, `TranslateReviewOut` | `schemas/review.py` |
| T5 | 6 routes (list/get/draft/translate/respond POST-PATCH-DELETE) + registration, ownership check via `_get_verified_app`, 503 on missing `ANTHROPIC_API_KEY` / no translator configured, territory→locale inference, `translate_with_cache` reuse | `api/v1/reviews.py`, `api/v1/__init__.py` |
| T6 | Frontend: reviews page, filter bar, drawer (translate / suggest-reply / post-update-delete), 7 TanStack Query hooks, route + sub-nav link | `pages/ReviewsPage.tsx`, `components/reviews/ReviewDrawer.tsx`, `lib/hooks.ts`, `types/index.ts`, `App.tsx`, `components/AppNavItem.tsx` |
| T7 | MCP tool parity (`app/mcp/tools/reviews.py`) — beyond spec's literal scope but present and mirrors the REST surface | `mcp/tools/reviews.py` |
| T8 | Backend test coverage: `ASCReviewService` request/response shapes, route-layer serialization helpers (`_serialize_review`, `_extract_cursor`, `_territory_to_locale`), schema char-limit/default validation — **new in this pass**, closes the only real gap found | `tests/test_reviews.py` |

T1–T7 were already implemented prior to this spec being closed out (found during verification, not written in this pass). T8 is the one gap this pass closed — the feature had zero test coverage despite being fully built.

## Acceptance Criteria
- [x] ASC Customer Reviews list with territory/rating filters, paginated via cursor — `test_reviews.py::test_list_reviews_*` (params + clamping); `has_response` is filtered in-memory in the route (ASC has no such filter) — verified by code inspection of `list_reviews()` in `api/v1/reviews.py`
- [x] View a single review's full text + existing response — `test_reviews.py::test_serialize_review_resolves_included_response`, `test_get_review_hits_expected_path_with_include`
- [x] AI-drafted reply via Claude Haiku 4.5, review-locale-aware, tone hint, never auto-posts — verified by code inspection of `draft_reply()` (no ASC write call in its body) + `/draft` route (only returns `DraftOut`, no `create_response` call); tone/theme prompt construction covered by `tests/services/test_review_reply_templates.py::test_prompt_includes_selected_theme_template`. Anthropic-SDK call itself is untested — no convention anywhere in this repo mocks `AsyncAnthropic` (same boundary as `AnthropicTranslator._complete`)
- [x] Translate review body to operator's locale via `translate_with_cache` — verified by code inspection of `/translate` route (calls `translate_with_cache` with `field_kind="description"`); same-locale short-circuit (no API call, `cached=True`) verified by code inspection
- [x] Create / update / delete a `customerReviewResponse` against ASC — `test_reviews.py::test_create_response_posts_expected_json_api_body`, `test_update_response_patches_expected_json_api_body`, `test_delete_response_issues_expected_delete`
- [x] Reply char-limit 5970 enforced — `test_reviews.py::test_reply_in_accepts_max_length`, `test_reply_in_rejects_over_max_length`, `test_response_body_max_len_matches_apple_documented_cap`
- [x] No `ANTHROPIC_API_KEY` → `/draft` returns 503 — verified by code inspection of `draft_review_reply()` (`if not settings.ANTHROPIC_API_KEY: raise HTTPException(503, ...)`); no HTTP-level test exists anywhere in this repo (no `TestClient` convention), consistent with the rest of the ASC-backed routers
- [x] Locale of review inferred by territory — `test_reviews.py::test_territory_to_locale_mapping` (known alpha-3 codes, case-insensitivity, unknown/`None` fallback to `en-US`)
- [x] Ownership verified before any ASC operation (`app.credential_id → credential.user_id == current_user_id`) — verified by code inspection: every route in `api/v1/reviews.py` calls `_get_verified_app(app_id, user_id, session)` before constructing an `ASCClient`
- [x] Frontend: reviews tab, filter bar, drawer with translate/suggest/save-update-delete, 5970-char counter — verified by code inspection of `ReviewsPage.tsx` + `ReviewDrawer.tsx`; `npx tsc --noEmit` clean; not manually click-tested in a browser
- [x] `npm test -- --run` and full backend `pytest` green — 382 backend tests passed (354 pre-existing + 28 new in `test_reviews.py`), 24 frontend tests passed, `tsc --noEmit` clean

**Judgment calls a human should double-check** (not verifiable by an automated test): AI draft tone quality (does "apologetic" actually read as noticeably softer than "neutral" for real Claude output?) and translation accuracy/naturalness for non-Latin-script locales (ja-JP, ar-SA, zh-Hans, etc.) — the code path is correct and tested, but output quality needs a human or LLM-judge pass with a live `ANTHROPIC_API_KEY`.
