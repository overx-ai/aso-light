---
id: 003
title: "Reviews module: translate swallows the quota signal into a generic 502"
status: open
severity: critical
created: 2026-08-26
updated: 2026-08-26
source: audit
repo: aso-light
files: backend/app/api/v1/reviews.py, backend/app/mcp/tools/reviews.py
---

# BUG 003 - Reviews module: translate swallows the quota signal into a generic 502

## Symptom

Unlike `app/api/v1/metadata.py:652-661` (which maps `TranslationQuotaExceededError → 429` and
`TranslatorUnavailableError → 502`), the reviews translate handler
(`app/api/v1/reviews.py:307-316`, MCP mirror `app/mcp/tools/reviews.py:294-310`) wraps the call in a
blanket `except Exception → 502 "AI translation service unavailable."`.

**Repro (expected → actual):** Hit the per-app AI-call cap, then call
`POST /apps/{id}/reviews/{review_id}/translate`. Expected: 429 with a clear "cap exceeded" message,
matching the metadata translation path. Actual: 502 "AI translation service unavailable" — the user is
told the service is down when it's actually rate-limiting them, and the cap message is lost.

Full analysis: `docs/014-reviews-module-security-findings.md` (finding C3).

## Root cause

The reviews translate handler's exception handling was never updated to match the specific-exception-first
pattern already established in `metadata.py` — it falls straight to the broad `except Exception` fallback.

## Fix

In both `app/api/v1/reviews.py` and `app/mcp/tools/reviews.py`, catch `TranslationQuotaExceededError → 429`
and `TranslatorUnavailableError → 502` (imported from `app.services.metadata.translate`) **before** the
broad fallback — same order as `metadata.py:652-661`. {filled in during implementation}

## Regression test

{the test that fails before the fix and passes after — this IS the TDD step, filled in during implementation}
