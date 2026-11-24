---
id: 020
title: "Niche finder: searched terms the store serves badly"
status: done
priority: p1
created: 2026-11-24
updated: 2026-11-24
repo: aso-light
release:
story:
depends_on: [014]
conflicts_with: []
---

# 020 - Niche Finder

> **TL;DR** — Shipped a standalone scan (no app needed) that expands up to 3 seed terms into their long tail,
> scores demand from Apple search hints and supply from the iTunes top 10, and ranks terms people search
> where no "real app" (on-title, 1000+ ratings, updated in 18 months) owns the results. MCP
> `keyword_intel_niche_scan` + `POST /keyword-intel/niches` (2 scans/min); read-only, no web page yet.

**Prerequisites**: [014 - Free keyword intel](014-free-keyword-intel.md), [010 - Keyword intelligence](../010-keyword-intelligence.md)

## Problem

Specs 014 and 019 score terms for an app that already exists. Nothing answers "what is searched but
badly served", which is where a new app (or a new feature page) has the cheapest path to rank.

## Design

New module `services/keyword_intel/niche.py`, reusing `FreeKeywordIntel` from 014.

1. **Expand.** Each seed → `seed_expansion` completions plus the long-tail probe `"{seed} {a..z}"`
   (completions that start with the probe). Deduped, capped at `MAX_NICHE_TERMS`.
2. **Demand.** `FreeKeywordIntel.popularity` (hint prefix depth, 0–100, cached 24h). Terms below
   `NICHE_MIN_POPULARITY` are dropped before the supply lookup.
3. **Supply.** iTunes top 10 per term. `search_apps` additionally returns `updated_at`
   (`currentVersionReleaseDate`) and `avg_rating`. A **real app** is a result whose title contains the
   term, with `rating_count >= REAL_APP_MIN_RATINGS` and updated within `STALE_DAYS`. On-title results
   failing either bar count as `weak_apps`.
4. **Score.** `opportunity = popularity * (100 - difficulty) / 100` (014's `organic_difficulty`), forced
   to 0 when `real_apps > NICHE_MAX_REAL_APPS`. Sorted by opportunity, then popularity.
5. **Surface.** MCP `keyword_intel_niche_scan(seeds, storefront)` (read-only, classified in
   `mcp/consent.py`), `POST /api/v1/keyword-intel/niches` (current-user auth, no app scope).

## Out of scope

- Web page — add once the scoring proves useful.
- Seedless "trending" discovery — Apple has no free trending-terms source.
- Persistence of scans.

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | `NicheFinder` + `classify_supply`, iTunes fields | dev | — | done | backend/app/services/keyword_intel/niche.py, backend/app/services/keywords/itunes_search.py, backend/app/schemas/keyword_intel.py |
| T2 | MCP tool, consent, REST route | dev | T1 | done | backend/app/mcp/tools/keywords.py, backend/app/mcp/consent.py, backend/app/api/v1/keyword_intel.py, backend/app/api/v1/__init__.py |
| T3 | Tests | test-writer | T1 | done | backend/tests/test_niche_finder.py |

## Acceptance

- A popular term whose top 10 are all off-title scores `real_apps == 0` and a positive opportunity.
- Three on-title, well-rated, fresh apps → opportunity 0; stale or under-rated on-title apps count as weak.
- Expansion dedupes, respects `MAX_NICHE_TERMS`, drops terms under `NICHE_MIN_POPULARITY`.
- Live scan on two seeds in US returns ranked niches with real numbers.

## Docs

`docs/010-keyword-intelligence.md`, `docs/007-mcp-integration.md` (new tool), CLAUDE.md tool count.
