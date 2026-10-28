---
id: 019
title: "Listing optimizer: brainstorm on add, market gap board, AI listing proposals, one-click localize, JSON export"
status: approved
priority: p1
created: 2026-10-26
updated: 2026-10-28
repo: aso-light
release:
story:
depends_on: [014]
conflicts_with: []
---

# 019 - Listing Optimizer

> **TL;DR** — We have keyword data (and, with [014](014-free-keyword-intel.md), scores without ASA) but
> no workflow that turns it into a listing. This spec adds that workflow: add an app → see the keywords
> it uses and the ones it's missing → see which markets lack the localized term → get three proposed
> name/subtitle/keyword-field sets for the main storefront → localize the chosen one into every live
> locale using the best-searched translation → preview and apply through the existing bulk path, or
> export it as JSON. Everything is suggestion-only and available over MCP.

**Prerequisites**: [014 - Free keyword intel](014-free-keyword-intel.md) (popularity blend, variant
picking, own-listing extraction, field packer), [004 - Localization management](../004-localization-management.md)

## Problem

| Step | Today | Gap |
|---|---|---|
| Brainstorm on add | apps sync only fills icons (`api/v1/apps.py`) | No `using` / `missing` split |
| Market view | `classify_keyword` (`services/metadata/coloring.py`) matches the English string | DE is "none" even when `kalorienzähler` is present; rankings refresh US only (`DEFAULT_TERRITORY_CODES` in `services/keywords/tracker.py`) |
| Main listing | MCP prompt `optimize_keywords` only, for outside LLMs | No candidates, no popularity delta |
| Localize | `metadata_translate`, literal, one variant | No pick by popularity |
| Export | prices only (`api/v1/export.py`) | No listing export |

## Design

New service package `services/listing/` with one service class; routes under `/apps/{id}/listing…`;
UI on a new `frontend/src/pages/ListingOptimizer.tsx` via hooks in `src/lib/hooks.ts`.

1. **Brainstorm on add.** After apps sync, a background job runs 014#6 (`using`), hints expansion and
   014#3 title mining, scored by 014#1/#2. `missing` = candidates with value above
   `MISSING_MIN_VALUE` that are not in `using`. Per live locale, `missing` terms get 014#4 variants.
   Stored as candidates (not auto-tracked); the user promotes them with the existing `keywords_add`.
   Same path works for a competitor app via the lookup source.
2. **Market gap board.** Keyword × market grid: rank from the existing per-territory rankings plus
   coverage. `classify_keyword` matches the locale's chosen variant (014#4) as well as the source term,
   and emits `missing_localized` when the source term ranks poorly in that market and its variant is
   absent from the listing. Rank refresh covers the app's live locales' storefronts, capped by
   `MAX_TRACKED_TERRITORIES` (replaces the hardcoded `["US"]` default for this path).
3. **Main-listing proposals.** User picks the main storefront (default US). The LLM (same translator
   stack as `translate.py`: Anthropic, OpenRouter fallback) gets the scored candidates and current
   listing and returns `LISTING_PROPOSALS` (3) {name, subtitle, keywords} sets. Each is validated by the
   014#5 packer rules (char limits, no word repeated across fields) and invalid ones are dropped, not
   repaired. Each shows Σ popularity covered vs the current listing. Suggestion-only, counted against
   the existing per-app monthly translation cap.
4. **One-click localize.** The chosen proposal → every live locale: translate with 014#4 variant
   picking, re-pack the keyword field per storefront (014#5, no repeats across a storefront's indexed
   locales). Output is a `BulkMetadataService.preview` payload; applying stays the existing
   `bulk/apply`. Respects `editable_fields` (a `READY_FOR_DISTRIBUTION` version only takes promo text).
5. **JSON export.** `GET /apps/{id}/metadata/export?source=current|proposal` → per-locale
   {name, subtitle, keywords, promotional_text, description} as a download.
6. **MCP.** `listing_brainstorm`, `listing_gaps`, `listing_propose`, `listing_localize` (all read-only;
   localize returns the preview payload), `metadata_export` (read-only). Apply stays
   `metadata_bulk_apply`. Classified in `mcp/consent.py`, underscore names.

## Out of scope

- Desktop app / user's own AI subscription, lifetime licensing — different product model.
- In-app chat and voice input — the MCP server is our agent surface.
- Keyword finder for Apple Search Ads campaigns — separate spec.
- Auto-apply of anything.

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | `ListingOptimizerService`: brainstorm, gaps, propose, localize | dev | — | todo | backend/app/services/listing/ |
| T2 | `classify_keyword` variant match + `missing_localized`; tracker territories from live locales | dev | — | todo | backend/app/services/metadata/coloring.py, backend/app/services/keywords/tracker.py |
| T3 | Routes `/apps/{id}/listing/*`, `GET /apps/{id}/metadata/export`, brainstorm after apps sync | dev | T1 | todo | backend/app/api/v1/listing.py, backend/app/api/v1/metadata.py, backend/app/api/v1/apps.py, backend/app/api/v1/__init__.py, backend/app/schemas/listing.py |
| T4 | MCP `listing_*`, `metadata_export` (read-only) | dev | T1 | todo | backend/app/mcp/tools/listing.py, backend/app/mcp/consent.py |
| T5 | Frontend page + hooks + route | dev | T3 | todo | frontend/src/pages/ListingOptimizer.tsx, frontend/src/lib/hooks.ts, frontend/src/App.tsx |
| T6 | Tests per Acceptance | test-writer | T4 | todo | backend/tests/ |

## Acceptance

- Unit tests: `missing` never contains a `using` term; `missing_localized` fires for a DE fixture
  (source term ranks low, variant absent) and not when the variant is present; every returned proposal
  passes the char limits and no-repeat rule; localize never writes a field outside `editable_fields`.
- Live check on one app (US main → DE, JA): brainstorm, gaps, propose, localize, preview end to end via
  MCP; nothing is written to ASC until `metadata_bulk_apply` is called.

## Docs

- Update `docs/003-keyword-analysis.md` and `docs/007-mcp-integration.md` (new tools).
- Once shipped, update `~/.claude/skills/aso/reference/keyword-research.md` to prefer
  `listing_brainstorm` / `listing_propose` and drop its "asolight does not extract competitor keywords"
  note (separate repo; spec there, not an edit from here).
