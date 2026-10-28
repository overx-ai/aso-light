---
item: docs/specs/014-free-keyword-intel.md
kind: spec
created: 2026-10-28
lane: go
---

## Approach
Pure scoring math (prefix-depth popularity, organic difficulty, tokenizer/n-grams, ASA blend, variant
pick) lives as plain functions in `services/keyword_intel/free.py`, with thin async fetchers around the
existing `ITunesSuggestionsService` / `ITunesSearchService` so tests mock only those two. `FreeIntelProvider`
joins `PROVIDER_FACTORIES` (after the ASA ones, so their fresh rows are readable for the blend) and writes
`Keyword.popularity` through a new no-op `after_upsert` hook on the ABC, keeping `fetch()` session-pure.
Translation variants reuse `translate_with_cache` unchanged by adding a `keyword_variants` field kind
(newline-separated output), so cache and monthly cap apply for free. The packer is a pure module.
Rejected: a new `translate_variants()` abstract method on `AbstractTranslator` — it would need its own
cache/cap path and every translator impl to change.

## Sequence
1. `app/data/stopwords.py` + tokenizer / n-grams (T1).
2. `free.py` pure scorers + fetchers, title mining, own-listing extraction (T2).
3. `popularity_source` column + migration, `after_upsert` hook, provider registration (T3).
4. `keyword_variants` field kind + `pick_variant` (T4).
5. `packer.py` (T5).
6. MCP tools + consent; remove `keywords_add_competitor_keywords` (T6).

## Files
| File | Change |
|---|---|
| `backend/app/data/stopwords.py` | new: per-language stopword sets |
| `backend/app/services/keyword_intel/free.py` | new: scorers, fetchers, mining, extraction, variant picker, provider |
| `backend/app/services/keyword_intel/packer.py` | new: pure field packer |
| `backend/app/services/keyword_intel/tokenize.py` | new: shared tokenizer / n-grams |
| `backend/app/services/keywords/tracker.py` | concurrency cap made public for reuse |
| `backend/app/services/keyword_intel/base.py` | `popularity_source` on `KeywordIntel`, `after_upsert` hook, upsert writes it |
| `backend/app/services/keyword_intel/service.py` | register provider, call `after_upsert`, expose `popularity_source` |
| `backend/app/models/keyword_intel.py` | `popularity_source` column |
| `backend/alembic/versions/d4b2f7a9c1e3_keyword_intel_popularity_source.py` | new migration |
| `backend/app/schemas/keyword_intel.py` | `popularity_source` on `KeywordIntelOut`; free-score / pack outputs |
| `backend/app/services/keywords/itunes_search.py` | `rating_count` in search result dicts |
| `backend/app/services/metadata/translate.py` | `keyword_variants` field kind, `TRANSLATION_VARIANTS` |
| `backend/app/mcp/tools/keywords.py` | `keyword_intel_score_free`, `keyword_intel_pack_fields`; alias removed |
| `backend/app/mcp/consent.py` | both new tools in `READ_ONLY` |
| `backend/tests/test_free_keyword_intel.py` | new tests |

## Tests first
- Prefix depth: score strictly decreases as the matching prefix gets longer; a miss scores 0.
- Difficulty rises with median rating count and with title-match share.
- Packer: ≤100 chars; no name/subtitle word; no term repeated across a storefront's indexed locales.
- Blend: ASA ≤ 5 or missing → hints value, source `hints`; ASA > 5 → ASA, source `asa`.
- Variant picker: best-scored variant wins, rest kept as alternatives; all miss → `not_searched_here`.
- Own-listing extraction: 1–3-grams, stopwords and brand token dropped.
- Free provider end to end with mocked iTunes services: row has `popularity_source`, `Keyword.popularity` written.

## Risks
- Hint budget: up to `len(t)` hint calls per term at the 80ms global throttle; capped by
  `MAX_FREE_TERMS` per call and a per-(term, storefront) in-process TTL cache.
- Token-sequence title matching does not segment CJK (a whole unspaced title is one token).
- Score is a relative proxy; no live Spearman check offline.

## Deviations
- Tokenizer lives in `services/keyword_intel/tokenize.py`, not `free.py`: `free.py` imports the packer, so a shared module avoids a cycle.
- `tracker._REFRESH_CONCURRENCY` renamed to public `REFRESH_CONCURRENCY` so free intel reuses the tracker's cap.
- Variant scoring uses at most `MAX_VARIANT_STOREFRONTS` (2) storefronts of the locale, packing storefront first, not every storefront: hint budget.
- `keyword_intel_pack_fields` caps at 10 terms and 4 locales; default locales are the storefront's indexed locales the app has metadata for, else the first.
- `Keyword.popularity` gets the app-independent hints score (Keyword rows are shared across apps); the ASA blend lives on the `free_hints` intel row.
- `keywords_add_competitor_keywords` removed rather than made to add: adding mined terms would duplicate `keyword_intel_score_free`; the REST POST is unchanged.
- Packer excludes name/subtitle words of every locale indexed in the storefront (cross-indexing), plus the optional planned `name`/`subtitle`.
- Seed-expansion terms take popularity from their position in the 2-letter hint list instead of a full prefix probe.
- `keyword_intel_pack_fields` is read-only for the listing but writes `MetadataTranslationCache` rows (the billed translation cache) via `translate_with_cache`.
- Own-listing extraction (#6) ships as `own_listing_terms` with no MCP surface yet (019 consumes it); only the pure extraction is tested.
- CLAUDE.md / docs/007 tool count (187 -> 188) and tool reference left to /docs.
