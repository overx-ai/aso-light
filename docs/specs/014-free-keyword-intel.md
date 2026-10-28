---
id: 014
title: "Free keyword intel: popularity proxy, organic difficulty, competitor title mining, native-term validation, field packer"
status: done
priority: p1
created: 2026-09-25
tasks: []
updated: 2026-10-28
repo: aso-light
release:
story:
depends_on: []
conflicts_with: []
---

# 014 - Free Keyword Intel

> **TL;DR** — Shipped a free Apple-hints provider: hints-prefix popularity (replaces ASA's floor-5 when
> ASA has no estimate, labelled `popularity_source`), organic difficulty, title mining, native-term variant
> picking and a 100-char field packer, via `keyword_intel_score_free` / `keyword_intel_pack_fields`. Live ASA
> correlation is not measured yet.

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
   **Blend with ASA (fixes the floor-5 problem):** ASA popularity at or below its floor (5) means
   "Apple has no estimate", not "no traffic". Use ASA when it is above the floor, otherwise the hints
   score. Each row carries `popularity_source` = `asa` | `hints`, so the UI can label it.
2. **Organic difficulty.** iTunes search top 10 for (term, storefront), same fetcher as
   `KeywordRankingTracker`. Inputs: median `userRatingCount` (log-scaled), share of titles containing
   the exact term. Output 0–100. Stored next to the ASA difficulty with its source labelled.
3. **Competitor title mining.** From the same top-N results plus tracked competitors: tokenize
   `trackName` into 1–2-grams, drop stopwords (per-locale list in `app/data/`) and the app's own brand
   token, rank by frequency. Returned as candidates, not auto-tracked. Subtitles are out of scope (not
   in the iTunes API).
4. **Native-term validation and variant picking.** The translator (`services/metadata/translate.py`)
   returns `TRANSLATION_VARIANTS` (3) candidates per term instead of one. Each variant is scored with #1
   in every storefront of `l` (`get_territories_for_locale`); the best-scoring variant wins, the rest
   are kept as alternatives. A term whose variants all miss is flagged `not_searched_here`. Seed
   expansion: hints for 2-letter prefixes of the top validated terms.
5. **Field packer.** Pure function: inputs candidates with (popularity, difficulty), name, subtitle,
   storefront. Value = popularity × (1 − difficulty/100). Drop words already in name/subtitle, greedy
   pack by value/len into ≤100 chars, comma-separated, no spaces. For a storefront, split across
   `get_indexed_locales_for_territory` without repeats. Output is a `metadata_bulk_preview` payload —
   never applied automatically.

6. **Own-listing extraction.** Tokenize the app's name, subtitle, keyword field and description into
   1–3-grams with #3's tokenizer and stopwords → the `using` set. Source: the ASC metadata snapshot when
   the app is connected, otherwise `ITunesSearchService.lookup_apps` (no keyword field or subtitle, so
   competitors yield name + description only). Feeds 019's brainstorm.

MCP: `keyword_intel_score_free` (read-only; #1–#3 for a term list) and `keyword_intel_pack_fields`
(read-only; #4–#5). Both classified read-only in `mcp/consent.py`.

Rate limit: reuse the tracker's concurrency cap; hint queries are the budget driver (≤len(t) per term),
so cap terms per call.

## Out of scope

- Absolute search volume (ASA-only).
- Subtitle scraping from the App Store web page — add if title mining proves thin.
- Scheduled rank refresh beyond US; metadata change history / impact attribution.

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Stopwords + tokenizer (1–3-grams, brand-token drop) | dev | — | done | backend/app/data/stopwords.py, backend/app/services/keyword_intel/free.py |
| T2 | Hints-prefix popularity, organic difficulty, title mining, own-listing extraction | dev | T1 | done | backend/app/services/keyword_intel/free.py |
| T3 | Register provider; ASA/hints blend + `popularity_source`; write `Keyword.popularity` | dev | T2 | done | backend/app/services/keyword_intel/service.py, backend/app/models/keyword_intel.py, backend/alembic/versions/ |
| T4 | Translation variants (`TRANSLATION_VARIANTS`) + variant picker | dev | T2 | done | backend/app/services/metadata/translate.py, backend/app/services/keyword_intel/free.py |
| T5 | Field packer (pure) | dev | — | done | backend/app/services/keyword_intel/packer.py |
| T6 | MCP `keyword_intel_score_free`, `keyword_intel_pack_fields` (read-only); fix `keywords_add_competitor_keywords` alias | dev | T3, T5 | done | backend/app/mcp/tools/keywords.py, backend/app/mcp/consent.py |
| T7 | Tests per Acceptance | test-writer | T6 | done | backend/tests/ |

## Acceptance

- Unit tests: prefix-depth → score is monotonic; difficulty rises with rating counts and title
  matches; packer never exceeds 100 chars, never repeats a name/subtitle word, never repeats across a
  storefront's indexed locales.
- Live check on one app (US, DE): Spearman rank correlation of free popularity vs ASA popularity on
  terms the app bids on is reported in the PR, plus the share of ASA floor-5 terms that get a non-floor
  hints score (no threshold; the number decides whether #1 ships as
  default or as a labelled guess).
- `keywords_add_competitor_keywords` (alias of list) removed or made to add keywords.
