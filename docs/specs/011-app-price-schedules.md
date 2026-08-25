---
id: 011
title: "App-level price schedules (paid apps) — read, preview, apply"
status: draft
created: 2026-08-17
tasks: []
---

# 011 - App-Level Price Schedules

> **Deliberately `draft`, not `approved`.** This is only needed if a **paid** app ships. Everything in the
> current portfolio is free-with-subscription, which the existing 44 pricing tools already cover fully.
> Approving it now would have a loop build a speculative module against an API nobody calls. Promote to
> `approved` the day a paid app exists — the design below is ready to go at that point.

## Problem

aso-light's pricing surface is thorough for **subscriptions** and **in-app purchases**: preview/apply,
price-point caches, GDP-bracket equalisation, export/import, intro offers. All 44 `pricing_*` MCP tools are
scoped to one of those two product types.

The **app's own price** is absent. `appPriceSchedules` and `appPricePoints` appear nowhere in the codebase.
For a paid app, the base price and every per-territory override can only be set by hand in App Store Connect
— including the initial price, which is also the one most likely to be set wrong across 175 territories.

## Scope

In:
- Read the current app price schedule, including scheduled future changes and per-territory overrides.
- Preview → apply a schedule: a base-territory (USA) price point plus per-territory overrides, in one
  schedule, following the existing preview-then-apply pattern used by subscription and IAP pricing.
- Reuse the existing GDP/bracket machinery (`services/pricing/`) rather than re-deriving equalisation.
- Export/import parity with `pricing_export_prices` / `pricing_import_prices` so a whole-portfolio reprice is
  one file.

Out (deferred):
- Introductory app pricing / limited-time price events.
- Pre-order pricing.
- Anything already covered for subscriptions and IAPs.

## ASC API surface used

- `GET /v1/apps/{id}/appPriceSchedule?include=baseTerritory,manualPrices,automaticPrices`
- `GET /v1/appPricePoints` (per territory, cacheable exactly like the existing IAP/subscription point caches)
- `POST /v1/appPriceSchedules` — base territory + manual per-territory prices in a single schedule

## Requirements — two rules that have cost real money

These are not documentation notes; encode them so the tool cannot get them wrong:

1. **Setting a new US base price posts a fresh auto-equalized schedule that WIPES every per-territory
   override.** Any reprice must therefore set the base price **first**, then re-apply the band/index
   adjustments on top — never the reverse. A reprice that skips the re-apply step silently reverts every
   market to Apple's auto-equalisation.
2. **Territory price points are discrete.** For each territory pick the nearest available point to
   `target = equalized_local × band_ratio`. Where no point is close, **skip that territory and report it** —
   never fail the whole run, never silently round to something far off.

Plus the house rules already applied elsewhere in this module:

3. **Plan before writing.** Compute the full territory plan and surface it (or dump it to a file) before any
   write, so a mid-run abort leaves either the old state or a logged, resumable position — never a
   half-applied mystery.
4. **A 2xx is not verification.** Read the schedule back and confirm the effective/scheduled prices.

## Tasks

| # | Task | Files |
|---|---|---|
| 1 | ASC client functions: get schedule, list app price points (with cache) | `backend/app/services/asc/pricing.py` |
| 2 | Plan builder reusing GDP/bracket logic + nearest-point resolution with skip-and-report | `backend/app/services/pricing/` |
| 3 | `pricing_get_app_price`, `pricing_preview_app_prices`, `pricing_apply_app_prices` MCP tools | `backend/app/mcp/tools/pricing.py` |
| 4 | Schemas | `backend/app/schemas/pricing.py` |
| 5 | Tests: base-price-wipes-overrides ordering, nearest-point skip/report, read-back verification | `backend/tests/` |

## Acceptance Criteria

- Applying a new base price followed by overrides leaves the overrides intact — proven by a test that would
  fail under the naive order.
- A territory with no nearby price point is reported as skipped, and the run still completes.
- Preview output shows every territory's current → target price before anything is written.
- Free apps are a clean no-op with an explanatory message, not an error.

## Cross-Repo Interfaces

Soft reference only. The `vibe-aso` skill (`~/.claude/skills/vibe-aso`, phase 4) documents paid-app pricing
as a **manual ASC step** and works fine without this; it would use these tools if they exist. No hard
dependency either way.
