---
id: 018
title: "listing_propose returns no proposals: Haiku 5.5 reasoning eats max_tokens, empty completion is cached"
status: open
priority: p1
severity: high
created: 2026-11-02
updated: 2026-11-02
source: live check
repo: aso-light
files: backend/app/services/metadata/translate.py
---

# BUG 018 - Reasoning model returns an empty completion, and the empty text is cached

> **TL;DR** — On `anthropic/claude-haiku-5.5` (bug 015's replacement) OpenRouter spends all 1024 `max_tokens`
> on reasoning and returns `content: ""` with `finish_reason: length`. The translator returned `""`, which was
> cached, so `listing_propose` gave `proposals: []` on every call. Reasoning is now disabled and an empty
> completion is a provider failure.

## Symptom
`listing_propose(app_id=3)` → `proposals: []`; cached `listing_proposals` row has `translated_text = ''`.
OpenRouter usage: `completion_tokens: 1024, reasoning_tokens: 1024`.

## Root cause
`OpenRouterTranslator._complete` returns `""` on an empty message and sends no `reasoning` setting.

## Fix

## Regression test
