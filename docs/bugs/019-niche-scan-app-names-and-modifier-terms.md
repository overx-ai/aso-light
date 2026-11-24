---
id: 019
title: "Niche scan ranks app names as niches and misses real apps behind modifier words"
status: open
priority: p1
created: 2026-11-24
updated: 2026-11-24
files: backend/app/services/keyword_intel/niche.py, backend/tests/test_niche_finder.py
---

# BUG 019 - Niche scan: app names and modifier words

> **TL;DR** — Live scan of `breathing`, `sleep sounds` (US) ranked existing app names ("breathing coach - nostril",
> "sleep sounds by sleep pillow") as top niches, and scored "sleep sounds free" at 0 real apps while 90k-rating
> "Sleep Sounds" apps own it. Cause: hints include navigational app titles, and on-title matching needs the exact phrase.

## Symptom

`NicheFinder().scan(["breathing", "sleep sounds"], "US")`, 2026-11-24:
- `breathing coach - nostril` (opportunity 41) — the term is the #1 result's title, verbatim.
- `sleep sounds free` real_apps=0, median ratings 90,459 — titles say "Sleep Sounds", never "sleep sounds free".

## Root cause

- Apple hints list app names as completions; only titles with ":" were filtered.
- `classify_supply` uses `contains_phrase(title, term)`, so a modifier word (free, app, best, for) the title
  doesn't carry makes every strong competitor invisible.

## Fix

## Regression test
