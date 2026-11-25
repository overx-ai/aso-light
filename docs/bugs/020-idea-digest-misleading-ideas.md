---
id: 020
title: "Idea digest announces crowded, rights-bound and app-name terms as ideas"
status: fixed
priority: p1
created: 2026-11-26
updated: 2026-11-26
files: backend/app/services/keyword_intel/niche.py, backend/app/services/idea_digest.py, backend/app/schemas/keyword_intel.py, backend/tests/test_niche_finder.py, backend/tests/test_idea_digest.py
---

# BUG 020 - Idea digest: misleading ideas

> **TL;DR** — 5 of the first 6 digest cards were not real openings: synonyms, twin app names, dominant apps and
> broadcast rights were not filtered. All four are now; nothing to do.

## Symptom

Cards of 2026-11-24/25, checked against the live US top 10:
- `ai music video maker` real_apps=0, while Yuna, Shoom and Enkio sell "AI Music Video Generator".
- `book tracker reading log tbr` is an app's title; a twin with the same words kept it.
- `live sports streaming free`: DAZN (614k ratings) and Fubo (112k) under a median of 3k, and broadcast rights.

## Root cause

- Title matching treats maker/generator/creator as different products.
- `is_app_name` takes any sibling covering the words as proof of a descriptive phrase, even an identical name.
- `IDEA_MAX_MEDIAN_RATINGS` cannot see one or two giants above the median.
- Nothing excludes terms whose content needs licences.

## Fix

- `niche.py`: maker/generator/creator match as one product (supply and digest dedup; difficulty keeps exact words).
- A sibling title of exactly the same words no longer proves a descriptive phrase.
- `giants`: top-10 apps with 50k+ ratings sharing at least half of the term's core words (minimum two). The digest
  rejects any; the on-demand scan only reports the count.
- `is_rights_bound`: sports/TV/film content with a delivery word, and music downloads, are neither topics nor ideas.
  Scores, schedules, guides and trackers stay.

Replay of the 2026-11-25 lookups: 5 of 6 cards dropped, `ai translate video` kept. Limits: English word lists; a
giant with an off-topic title is missed (Finch on `habit tracker game`); AR drawing terms are dropped too (98k leader).

## Regression test

`backend/tests/test_niche_finder.py`, `backend/tests/test_idea_digest.py`.
