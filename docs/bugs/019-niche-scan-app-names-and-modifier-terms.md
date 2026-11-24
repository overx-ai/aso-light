---
id: 019
title: "Niche scan ranks app names as niches and misses real apps behind modifier words"
status: fixed
priority: p1
created: 2026-11-24
updated: 2026-11-24
files: backend/app/services/keyword_intel/niche.py, backend/tests/test_niche_finder.py
---

# BUG 019 - Niche scan: app names and modifier words

> **TL;DR** — Niche scans ranked app names and developer names as niches and missed competitors behind words
> like "free". Fixed in `niche.py` with name filters and modifier-free title matching; empty search results no longer
> look like open niches. Non-segmented scripts (ja/zh) remain unreliable.

## Symptom

`NicheFinder().scan(["breathing", "sleep sounds"], "US")`, 2026-11-24:
- `breathing coach - nostril` (opportunity 41) — the term is the #1 result's title, verbatim.
- `sleep sounds free` real_apps=0, median ratings 90,459 — titles say "Sleep Sounds", never "sleep sounds free".

## Root cause

- Apple hints list app names as completions; only titles with ":" were filtered.
- `classify_supply` uses `contains_phrase(title, term)`, so a modifier word (free, app, best, for) the title
  doesn't carry makes every strong competitor invisible.

## Fix

- On-title matching compares the term's words minus `NICHE_MODIFIERS` with the title's word set.
- Hint-derived terms (never the user's seeds) are dropped when they are the #1 result's name and no other
  top-10 title carries the same words, or when they end in a company suffix.
- A term with an empty iTunes result is skipped: `search_apps` returns `[]` on HTTP errors, which scored as
  the best possible niche.

Known limits: an app name shared by a sibling title is kept ("breathing zone"); a lone generic on-title app at #1
drops its term; modifiers are English only; ja/zh titles rarely match, so crowded terms look open; `difficulty`
still uses the exact phrase, so modifier terms score easier than they are.

## Regression test

`backend/tests/test_niche_finder.py`: modifier words, app names, developer names, empty results.
