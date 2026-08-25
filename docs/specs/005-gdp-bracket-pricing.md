---
id: 005
title: "GDP-Bracket Pricing Strategy"
status: done
created: 2026-04-26
updated: 2026-08-26
tasks: []
---

# 005 - GDP-Bracket Pricing Strategy

## Problem
Existing pricing strategies (PPP, Big Mac, Netflix, Spotify, exchange rate) all do **proportional** scaling from a single USD baseline. There's no way to set **absolute prices per tier** — e.g. "all top-income countries pay $9.99, mid-income $4.99, low-income $1.99, and a custom country list (RU/BY/KZ/UA/PL) pays $2.99 regardless of GDP."

## Requirements
- New strategy `index_type = "gdp_brackets"` slotted into the existing preview/apply/preset flow
- 4 tiers: `top` / `mid` / `low` / `special`; each gets an absolute USD price (no multipliers)
- Tier assignment rule (in priority order):
  1. If territory in special list → `special`
  2. Else if territory in manual_overrides → that tier
  3. Else GDP/capita PPP threshold → top/mid/low
  4. Else (no GDP data) → `low`
- GDP source: World Bank `NY.GDP.PCAP.PP.CD` (PPP, current international $) — store **raw** values
- Special list and full config persisted **per pricing preset**
- Reuse existing currency conversion + smart rounding + Apple price-point matching + safety bands (±50%, `SAFETY_BAND_PCT`)
- v1: subscriptions only (IAP follow-up uses identical pattern)

## Architecture

### New `IndexFetcher`: `GDPFetcher`
File: `backend/app/services/indices/gdp.py`. Same World Bank pattern as `PPPFetcher`, but stores raw GDP/capita PPP (no US-normalization) under `index_type='gdp_per_capita_ppp'`. Registered in `IndexRefreshService` so the existing `POST /api/v1/indices/refresh?index_type=gdp_per_capita_ppp` works.

### Preset model: add `config` JSON column
`PricePreset.config: dict | None` — nullable JSON column. Used only by strategies that need extra config (just `gdp_brackets` for now). Older preset rows have `config=NULL` and continue to work.

### `GDPBracketConfig` schema
```python
class GDPBracketConfig(BaseModel):
    tier_prices_usd: dict[Literal["top", "mid", "low", "special"], Decimal]
    tier_thresholds_usd: dict[Literal["top_min", "mid_min"], Decimal]
    manual_overrides: dict[str, Literal["top", "mid", "low", "special"]] = {}
    special_territories: list[str] = []
```
Validators: 4 tier prices required; `top_min > mid_min`; alpha-2 codes only.

### Strategy dispatch
`PricePreviewRequest.index_type` Literal extended to include `"gdp_brackets"` plus optional `gdp_config: GDPBracketConfig`. `backend/app/api/v1/pricing.py` adds a `gdp_brackets` branch parallel to `exchange_rate`. Apply path is unchanged (already accepts `[{territory_code, price_point_id}]`).

### `assign_tier()` helper
`backend/app/services/pricing/gdp_brackets.py` — pure function returning the tier name from `(territory_code, gdp_value, config)`. Drives both the preview helper and unit tests.

### GDP browser endpoint
`GET /api/v1/indices/gdp` — returns `[{territory_code, territory_name, gdp_per_capita_ppp, currency_code}, ...]` sorted desc. Fuels the frontend's tier-assignment table.

### Frontend
- `PriceMultiplierPanel` adds "GDP Brackets" option. When selected, single-base-price input is replaced by a "Configure brackets" button.
- New `GDPBracketEditor` modal: 4 USD price inputs, 2 GDP threshold inputs, special-list multi-select, mantine-datatable of all territories with tier badge + per-row override Select, live tier-count summary, "Refresh GDP data" button.
- `PresetManager` carries `config` through save/load.

## Edge Cases & Risks
| Case | Mitigation |
|------|------------|
| Territory has no GDP data | Falls back to `low` tier (per user decision) |
| Manual override conflicts with special list | Special list wins (priority 1) |
| Top/mid thresholds inverted in payload | Pydantic validator rejects (422) |
| Existing preset rows have no `config` | Column nullable; legacy strategies unaffected |
| GDP data not yet refreshed | `GET /indices/gdp` returns empty; UI shows "Refresh GDP data" prompt |
| World Bank API down during refresh | Existing fetcher pattern logs & returns empty; partial refresh handled by `_save_records()` |

## Tasks
| ID | Description | Files |
|----|-------------|-------|
| T1 | `GDPFetcher` (raw PPP) + register | `services/indices/gdp.py`, `services/indices/refresh.py` |
| T2 | `config` JSON column + Alembic migration + schema | `models/preset.py`, `schemas/preset.py`, `alembic/versions/001_*.py` |
| T3 | `GDPBracketConfig` schema, extend `PricePreviewRequest` | `schemas/pricing.py` |
| T4 | `assign_tier()` + `_preview_gdp_brackets()` | `services/pricing/gdp_brackets.py` |
| T5 | Wire dispatch in subscription preview endpoint | `api/v1/pricing.py` |
| T6 | `GET /api/v1/indices/gdp` endpoint | `api/v1/indices.py` |
| T7 | Backend unit tests | `tests/test_gdp_brackets.py` |
| T8 | Frontend types + hooks | `types/index.ts`, `lib/hooks.ts` |
| T9 | `GDPBracketEditor` modal | `components/pricing/GDPBracketEditor.tsx` |
| T10 | Wire `PriceMultiplierPanel` strategy switch | `components/pricing/PriceMultiplierPanel.tsx` |
| T11 | `PresetManager` `config` plumbing | `components/pricing/PresetManager.tsx` |

## Acceptance Criteria
- [x] `POST /api/v1/indices/refresh?index_type=gdp_per_capita_ppp` populates `economic_indices` with raw GDP/capita PPP (verified by code inspection — no endpoint-level test exists for any `IndexFetcher`, consistent with the rest of the codebase)
- [x] `GET /api/v1/indices/gdp` returns sorted territory list with GDP values (verified by code inspection — same no-endpoint-test convention as above)
- [x] Preview with `index_type='gdp_brackets'` returns per-territory prices grouped by tier — `test_gdp_bracket_preview_endpoint.py`
- [x] `assign_tier()` priority order: special > manual override > threshold > fallback `low` — `test_gdp_brackets.py`
- [x] Pydantic rejects `top_min < mid_min` with 422 — `test_gdp_brackets.py::test_inverted_thresholds_rejected`
- [x] UI: GDP browser modal opens, tier badges update live as thresholds/overrides change (verified by code inspection of `GDPBracketEditor.tsx` + `PriceMultiplierPanel.tsx` wiring — not manually click-tested in a browser)
- [x] Save/load preset persists full `GDPBracketConfig` end-to-end (verified by code inspection — `PriceMultiplierPanel.tsx` save/load path)
- [x] Apply path unchanged — existing safety bands and price-point matching reused — `test_gdp_bracket_preview_endpoint.py`
