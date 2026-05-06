---
id: 008
title: "Review responses — read, AI-suggest, translate, post"
status: draft
created: 2026-05-06
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
- `PATCH /apps/{app_id}/reviews/{review_id}/respond` body `ReplyIn` → `ReviewResponseOut`
- `DELETE /apps/{app_id}/reviews/{review_id}/respond` → 204

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
- Hooks in `lib/hooks.ts`: `useReviews`, `useReview`, `useDraftReply`, `useTranslateReview`, `useRespondToReview`, `useUpdateReply`, `useDeleteReply`.

## Edge cases

- **Reviews without bodies** — Apple sometimes returns rating-only entries. Show "(no review text)" in the list; AI draft / translate disabled.
- **Already-responded** — the existing reply is editable (PATCH) or removable (DELETE). UI shows the existing reply and toggles between Save/Update.
- **Rate limits** — ASC throttles the customer-reviews endpoint heavily. Use the existing rate-limiter + 429 backoff in `ASCClient`. Frontend stale-time 60s.
- **No `ANTHROPIC_API_KEY`** — `/draft` returns 503; UI hides the Suggest button with a tooltip.
- **Char limit 5970** — schema `Field(max_length=5970)` + frontend live counter.
- **Locale of review** — Apple returns territory but not language; we infer locale by territory's primary language (reuse `app/data/territories.py` mapping).

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
