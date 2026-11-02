---
id: 015
title: "AI translation fails everywhere: OpenRouter model anthropic/claude-3.5-haiku retired"
status: fixed
priority: p0
severity: critical
created: 2026-11-02
updated: 2026-11-02
source: live check
repo: aso-light
files: backend/app/core/config.py, backend/.env.example
---

# BUG 015 - AI translation fails everywhere: OpenRouter model retired

> **TL;DR** — OpenRouter answers 404 for `anthropic/claude-3.5-haiku`, the default `OPENROUTER_TRANSLATION_MODEL`,
> so every AI call (metadata_translate, review replies, keyword variants, listing proposals) fails. Variants fall
> back to the English term silently. Default moves to `anthropic/claude-haiku-5.5`; local `.env` must follow.

## Symptom
`keyword_variants(..., "breathing exercises", "en-US", "de-DE")` → `[]`, log: `Translator openrouter:anthropic/claude-3.5-haiku failed (Client error '404 Not Found')`.
`listing_brainstorm` on app 3 returned DE/ES variants equal to the English term with no alternatives.

## Root cause
The model id was retired on OpenRouter; nothing else changed.

## Fix
`OPENROUTER_TRANSLATION_MODEL` default and `.env.example` → `anthropic/claude-haiku-5.5` (`backend/app/core/config.py`). A local `.env` that pins the old id must be edited by hand.

## Regression test
Live: `keyword_variants` for "breathing exercises" en-US→de-DE returns `atemübungen, atemtechnik, atmen üben`. Config-only; no unit test (a model id is not behavior).
