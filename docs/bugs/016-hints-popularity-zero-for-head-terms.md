---
id: 016
title: "Free popularity scores head terms 0 (\"breathing\") because hints never list the bare term"
status: fixed
priority: p1
severity: high
created: 2026-11-02
updated: 2026-11-02
source: live check
repo: aso-light
files: backend/app/services/keyword_intel/free.py
---

# BUG 016 - Free popularity scores head terms 0

> **TL;DR** — `keyword_intel_score_free` gave "breathing" popularity 0 (vs 69 for "box breathing"). Apple's hints
> list longer completions ("breathing app", "breathing exercises") instead of the bare head term, and the walk
> required an exact match. A hint starting with the term as a whole word now counts as a match.

## Symptom
US hints for `breat` … `breathing` never contain `breathing` itself; score = 0.

## Root cause
`FreeKeywordIntel.popularity` matched `normalized in hints` only.

## Fix
`FreeKeywordIntel.popularity` takes the first hint equal to the term or starting with `term + " "` (word boundary, so "breath" does not match "breathwork").

## Regression test
`test_popularity_counts_a_hint_that_extends_the_whole_term`. Live: "breathing" 0 → 56.
