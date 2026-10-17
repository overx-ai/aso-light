---
id: 014
title: "Free keyword intel: popularity proxy, organic difficulty, competitor title mining, native-term validation, field packer"
status: draft
created: 2026-09-25
updated: 2026-09-25
tasks: []
---

# 014 - Free Keyword Intel

> **TL;DR** — Without ASA spend, keyword research today has no popularity for an arbitrary term, no
> organic difficulty, no competitor mining, no native-term check and no field packer. All five can be
> built from two free Apple endpoints we already call: search hints (MZSearchHints) and iTunes search.
> Output is a relative ranking, not search volume — Apple publishes volume only via ASA.

**Prerequisites**: [003 - Keyword analysis](../003-keyword-analysis.md), [010 - Keyword intelligence](../010-keyword-intelligence.md)

## Problem

| Step | Today | Gap |
|---|---|---|
| Popularity | ASA recs/search terms only (`services/keyword_intel/`); hints give order, no score | `Keyword.popularity` exists (`models/keyword.py:24`), never written |
| Difficulty | `asa_search_terms._difficulty_from_cpt_ttr` — auction pressure | No organic difficulty |
| Competitors | Rank matrix on own tracked keywords, US only | No title mining |
| Native terms | Literal LLM translation (`services/metadata/translate.py`) | No check that a term is searched in that storefront |
| Fields | Coverage, audit, bulk apply | No automatic 100-char pack, no cross-locale reuse |

## Design

One service, `services/keyword_intel/free.py`, registered as a third provider beside the ASA ones in
`services/keyword_intel/service.py` so results flow through the existing cache and `keyword_intel_list`.

1. **Popularity (hints prefix depth).** For term `t` in storefront `s`, query hints for prefixes of `t`
   (shortest first, stop on hit) via `ITunesSuggestionsService.get_suggestions`. Score from shortest
   matching prefix length relative to `len(t)` and position in the list → 0–100. Write
   `Keyword.popularity` + `popularity_updated_at`. Cache per (term, storefront).
2. **Organic difficulty.** iTunes search top 10 for (term, storefront), same fetcher as
   `KeywordRankingTracker`. Inputs: median `userRatingCount` (log-scaled), share of titles containing
   the exact term. Output 0–100. Stored next to the ASA difficulty with its source labelled.
3. **Competitor title mining.** From the same top-N results plus tracked competitors: tokenize
   `trackName` into 1–2-grams, drop stopwords (per-locale list in `app/data/`) and the app's own brand
   token, rank by frequency. Returned as candidates, not auto-tracked. Subtitles are out of scope (not
   in the iTunes API).
4. **Native-term validation.** For each candidate in locale `l`, run #1 against each storefront of `l`
   (`get_territories_for_locale`). Terms with no hints hit are flagged `not_searched_here`. Seed
   expansion: hints for 2-letter prefixes of the top validated terms.
5. **Field packer.** Pure function: inputs candidates with (popularity, difficulty), name, subtitle,
   storefront. Value = popularity × (1 − difficulty/100). Drop words already in name/subtitle, greedy
   pack by value/len into ≤100 chars, comma-separated, no spaces. For a storefront, split across
   `get_indexed_locales_for_territory` without repeats. Output is a `metadata_bulk_preview` payload —
   never applied automatically.

MCP: `keyword_intel_score_free` (read-only; #1–#3 for a term list) and `keyword_intel_pack_fields`
(read-only; #4–#5). Both classified read-only in `mcp/consent.py`.

Rate limit: reuse the tracker's concurrency cap; hint queries are the budget driver (≤len(t) per term),
so cap terms per call.

## Out of scope

- Absolute search volume (ASA-only).
- Subtitle scraping from the App Store web page — add if title mining proves thin.
- Scheduled rank refresh beyond US; metadata change history / impact attribution.

## Acceptance

- Unit tests: prefix-depth → score is monotonic; difficulty rises with rating counts and title
  matches; packer never exceeds 100 chars, never repeats a name/subtitle word, never repeats across a
  storefront's indexed locales.
- Live check on one app (US, DE): Spearman rank correlation of free popularity vs ASA popularity on
  terms the app bids on is reported in the PR (no threshold; the number decides whether #1 ships as
  default or as a labelled guess).
- `keywords_add_competitor_keywords` (alias of list) removed or made to add keywords.
