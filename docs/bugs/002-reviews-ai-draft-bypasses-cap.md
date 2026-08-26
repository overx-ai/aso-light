---
id: 002
title: "Reviews module: AI draft path bypasses the 500-call/month cap"
status: open
severity: critical
created: 2026-08-26
updated: 2026-08-26
source: audit
repo: aso-light
files: backend/app/api/v1/reviews.py, backend/app/mcp/tools/reviews.py, backend/app/services/reviews/draft.py, backend/app/services/metadata/translate.py
---

# BUG 002 - Reviews module: AI draft path bypasses the 500-call/month cap

## Symptom

`draft_review_reply` (`app/api/v1/reviews.py:234-242`, MCP mirror `app/mcp/tools/reviews.py:236-244`)
calls `draft_reply(...)` directly instead of going through `translate_with_cache`. CLAUDE.md documents a
per-app 500-call/30-day soft cap for AI calls (`MetadataTranslationCache`), but the draft path has **no
cap check, no counter, nothing recorded** — every draft is an uncapped, uncached Anthropic call.

**Repro (expected → actual):** Call `POST /apps/{id}/reviews/{review_id}/draft` (or the MCP equivalent)
more than 500 times in a rolling 30-day window for one app. Expected: 429 once the cap is hit, matching
the metadata translation path. Actual: unlimited calls succeed — spend-DoS risk and a contract violation
of the documented cap.

Full analysis: `docs/014-reviews-module-security-findings.md` (finding C2).

## Root cause

`draft_reply()` was wired directly to the Anthropic client, bypassing the shared cap/record machinery that
`translate_with_cache` provides for every other AI-touching path in the app.

## Fix

Extract the cap check/record logic from `translate_with_cache` into a reusable
`enforce_and_record_ai_call(session, app_id, kind="draft")` — a usage counter scoped to `app_id` (drafts
are non-deterministic, so this needs a counter, not the content cache `translate_with_cache` uses). Call
it before `draft_reply(...)` in both `app/api/v1/reviews.py` and `app/mcp/tools/reviews.py`, raising
`TranslationQuotaExceededError` on cap-exceeded (mapped to HTTP 429 / MCP `ToolError` — see BUG 003 for the
correct error-mapping this depends on). {filled in during implementation}

## Regression test

{the test that fails before the fix and passes after — this IS the TDD step, filled in during implementation}
