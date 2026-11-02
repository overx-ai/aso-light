---
id: 017
title: "listing_brainstorm \"missing\" is filled with unrelated store-wide searches (costco, coinbase)"
status: fixed
priority: p1
severity: high
created: 2026-11-02
updated: 2026-11-02
source: live check
repo: aso-light
files: backend/app/services/keyword_intel/free.py
---

# BUG 017 - Brainstorm expansion returns store-wide top searches

> **TL;DR** — On Refresher, `missing` was led by "cooking games", "collectr", "coinbase", "costco". Seed expansion
> probed the first 2 letters of each seed ("co" from "coherence"), which returns the store's generic top queries.
> Expansion now probes the whole seed term, so hints are its own completions.

## Symptom
`listing_brainstorm(app_id=3, refresh=true)` → top `missing` terms unrelated to breathing/HRV.

## Root cause
`seed_expansion` used `SEED_PREFIX_LEN = 2`.

## Fix
`seed_expansion` probes the whole seed, keeps only true completions (no app titles with `:`), and scores each from the seed's own popularity scaled by hint rank.

## Regression test
`test_seed_expansion_keeps_completions_scored_below_their_seed`, `test_seed_expansion_skips_app_titles`. Live: `missing` now led by coherence/wim hof/anxiety relief/breathing exercises.
