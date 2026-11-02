---
id: 017
title: "listing_brainstorm \"missing\" is filled with unrelated store-wide searches (costco, coinbase)"
status: open
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

## Regression test
