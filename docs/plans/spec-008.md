# Plan: spec-008 — Review Responses

## Approach

Verdict, based on actually reading every file (not grepping): **fully implemented**, with one real gap — **zero test coverage** for the new backend surface (`ASCReviewService`, the `/apps/{id}/reviews...` routes, and the schemas). Everything the spec describes exists in the codebase and matches the spec's architecture section almost exactly:

- `backend/app/services/asc/reviews.py` — `ASCReviewService` with `list_reviews`, `get_review`, `create_response`, `update_response`, `delete_response`, `RESPONSE_BODY_MAX_LEN = 5970`. Matches spec's method signatures.
- `backend/app/services/reviews/draft.py` — `draft_reply(...)` via `AsyncAnthropic` directly (Haiku 4.5), suggestion-only (never posts). Matches spec.
- `backend/app/services/reviews/templates.py` — a review-theme classifier (`classify_review_theme`) and per-theme reply templates. This is an addition **beyond** spec 008's text, but it's small, self-contained, already fully wired, and improves draft quality — not the separate docs/009 sentiment-analytics feature (no DB model, no charts, no `ReviewTheme` DB table involved here beyond the `app/models/review_theme.py` used by the *other* feature). I'm treating it as already-shipped scope, not something to touch or duplicate.
- `backend/app/schemas/review.py` — `ReviewOut`, `ReviewResponseOut`, `ReviewListOut`, `ReplyIn` (`Field(min_length=1, max_length=5970)`), `DraftIn`, `DraftOut`, `TranslateReviewIn`, `TranslateReviewOut`. Matches spec exactly, plus a `theme` field (harmless addition).
- `backend/app/api/v1/reviews.py` — all 6 routes from the spec exist and are registered (`app/api/v1/__init__.py:46`). Ownership is verified via `_get_verified_app` on every route, per CLAUDE.md convention. `/draft` returns 503 when `settings.ANTHROPIC_API_KEY` is unset. `/translate` reuses `translate_with_cache` + `build_translator`, returns 503 when no translator is configured, and short-circuits (no API call, `cached=True`) when source locale == target locale. Locale inference by territory exists as `_TERRITORY_TO_LOCALE` (alpha-3 → BCP-47), consistent with the spec's "infer by territory" edge case.
- Frontend: `ReviewsPage.tsx` (280 lines) + `ReviewDrawer.tsx` (365 lines), 7 hooks in `hooks.ts` (`useReviews`, `useReview`, `useDraftReply`, `useTranslateReview`, `useCreateReply`, `useUpdateReply`, `useDeleteReply` — named slightly differently than the spec's `useRespondToReview` but functionally identical), types in `types/index.ts`, route in `App.tsx`, nav link in `AppNavItem.tsx`. All present.

**One real, spec-relevant deviation** (not a bug, just worth recording): the spec's Architecture section describes `PATCH /respond` and `DELETE /respond` (implying the review ID alone identifies the response to mutate). The actual implementation uses `PATCH/DELETE /apps/{app_id}/reviews/{review_id}/respond/{response_id}` — it requires the response ID explicitly. This is *more* correct: `GET /reviews/{id}` already returns `response.id`, and requiring it in the URL avoids an extra ASC round-trip inside the route to resolve "the" response for a review. Frontend hooks already call it this way. I'm treating this as the spec's own documented Architecture text being slightly stale versus what got built, not a defect — no code change needed, just recorded in the spec update as a deviation.

**The real gap**: `backend/tests/` has exactly one review-related test file, `tests/services/test_review_reply_templates.py`, which only covers `classify_review_theme` and `_build_system_prompt`. There is **no test coverage at all** for `ASCReviewService` (list/get/create/update/delete against ASC), for the route-layer pure functions (`_serialize_review`, `_extract_cursor`, `_territory_to_locale`), or for the `ReplyIn`/`DraftIn`/`TranslateReviewIn` schema validation (char limits, defaults). Given a draft-status spec with no Tasks/AC table, this is exactly the kind of loop nobody closed. I will close it with TDD-style tests that mirror the codebase's established pattern (`tests/test_experiment.py`'s `FakeASCClient` + `run_async` harness — this repo has **no** FastAPI `TestClient`/`AsyncClient(app)` convention anywhere, so I will not introduce one; route-level HTTP behavior like the 503-on-missing-key branch is simple `if`/`raise HTTPException` code identical in shape to the untested metadata-translate route, consistent with the rest of the codebase's coverage boundary).

Note: `draft_reply()` and `AnthropicTranslator._complete()` call the Anthropic SDK directly — the codebase has no convention anywhere for mocking `AsyncAnthropic` (verified by grep: zero hits). I will not invent one; this stays an untested boundary consistent with the rest of the repo (e.g. `AnthropicTranslator._complete` is equally untested today).

## Sequence

1. Write `backend/tests/test_reviews.py`:
   - `ASCReviewService.list_reviews` — territory uppercased into `filter[territory]`, rating stringified into `filter[rating]`, cursor passthrough, limit clamped to [1, 200], `include=response` always sent.
   - `ASCReviewService.get_review` — correct path + `include=response`.
   - `ASCReviewService.create_response` — JSON:API body shape (type, attributes.responseBody, relationships.review).
   - `ASCReviewService.update_response` — JSON:API body shape (type, id, attributes.responseBody).
   - `ASCReviewService.delete_response` — correct DELETE path.
   - `app.api.v1.reviews._serialize_review` — with/without an `included` response resolves `ReviewOut.response`; missing body → `body=None`; theme classification is invoked.
   - `app.api.v1.reviews._extract_cursor` — parses `links.next` cursor token; returns `None` when absent.
   - `app.api.v1.reviews._territory_to_locale` — known alpha-3 codes map correctly; unknown/None falls back to `en-US`.
   - `ReplyIn` — accepts 5970 chars, rejects 5971 and empty string (422/ValidationError).
   - `DraftIn` — defaults to `tone="neutral"`, `theme=None`.
   - `TranslateReviewIn` — rejects single-character locale.
2. Run the full backend suite; fix any real failures found (expect none — implementation looked correct on read, but TDD discipline means verifying, not assuming).
3. Frontend: run `npx tsc --noEmit` and `npm test -- --run`; fix any real failures found.
4. Update `docs/specs/008-review-responses.md`: add `## Tasks` table + `## Acceptance Criteria` checklist, flip `status: draft` → `status: done`, add `updated: 2026-08-26`.
5. Commit.

## Files

- New: `backend/tests/test_reviews.py`
- Edit: `docs/specs/008-review-responses.md`
- New: `docs/plans/spec-008.md` (this file)

No production code changes expected — this is a verification + spec-completion pass, not a feature-gap pass.

## Tests first

`backend/tests/test_reviews.py` is written before any production-code change. Given my read-through found the implementation already correct, I expect all new tests to pass immediately; if any fails, that's a genuine bug in already-"shipped" code and I fix the production file (not the test) unless the test itself is wrong per the spec.

## Risks

- AI draft/translate quality (tone accuracy, locale correctness) is a judgment call, not something a unit test can verify — flagged for human review in the final report.
- `draft_reply` / `AnthropicTranslator` Anthropic-SDK boundary remains untested, consistent with the rest of the repo. Not treated as a gap to close in this pass since it would require inventing a new test convention not used anywhere else in `backend/tests/`.

## Deviations

- Spec's Architecture section says `PATCH/DELETE .../respond`; actual routes are `PATCH/DELETE .../respond/{response_id}`. Recorded as an intentional, more-correct deviation (see Approach above) — no code change.
- Frontend hook names differ slightly from spec text (`useCreateReply` vs spec's `useRespondToReview`); functionally identical, not worth renaming.
- `theme` classification (`ReviewTheme`, `DraftIn.theme`, `DraftOut.theme`) is additional surface beyond the spec's literal text but is small, already shipped, and improves the AI draft — left as-is.
