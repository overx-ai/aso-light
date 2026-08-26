---
id: 004
title: "Reviews module: translation-cache cross-feature bleed via reused field_kind"
status: open
severity: critical
created: 2026-08-26
updated: 2026-08-26
source: audit
repo: aso-light
files: backend/app/api/v1/reviews.py, backend/app/mcp/tools/reviews.py, backend/app/models/metadata.py, backend/app/services/metadata/translate.py
---

# BUG 004 - Reviews module: translation-cache cross-feature bleed via reused field_kind

## Symptom

Review translate (`app/api/v1/reviews.py:305`, `field_kind="description"  # type: ignore`, MCP mirror)
reuses the metadata `"description"` field kind instead of having its own. Same app, review body text
identical to a previously-translated app-description string → the review translate call returns the
cached **metadata description** translation (and vice-versa) — content bleed across features within one
app.

**Repro (expected → actual):** Translate an app's description containing text T to locale L (gets cached
under `field_kind="description"`). Then translate a review body that happens to contain the exact same
text T to the same locale L. Expected: independent translation for the review context. Actual: cache hit
returns the metadata-description translation verbatim.

Combined with BUG 001 (cross-app IDOR), app-B review text could end up persisted under app-A's cache
namespace via the description `field_kind`, though the `app_id` key itself prevents a *direct* cross-tenant
read — the leak axis here is the *content source*, not the app id.

Full analysis: `docs/014-reviews-module-security-findings.md` (finding C4).

## Root cause

No dedicated `field_kind` was ever added for reviews; the review translate path reused the closest existing
one (`"description"`) with a `# type: ignore` rather than extending the `FieldKind` enum.

## Fix

Add a dedicated `"review_body"` entry to `FieldKind` / `FIELD_CHAR_LIMITS` in `app/models/metadata.py`
(and wherever `FIELD_CHAR_LIMITS` is otherwise declared), and use it for review translations in both
`app/api/v1/reviews.py` and `app/mcp/tools/reviews.py` — drop the `field_kind="description"` reuse and the
`# type: ignore`. {filled in during implementation}

## Regression test

{the test that fails before the fix and passes after — this IS the TDD step, filled in during implementation}
