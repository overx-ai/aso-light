# 001 - Pricing System

**Prerequisites**: [000 - Architecture](000-architecture.md)

## Overview

The pricing system is the core feature of ASO-Light. It reads current App Store prices via the ASC API, calculates suggested prices using economic indices or live exchange rates, and applies bulk price changes across all 175+ territories.

## Price Calculation Pipeline

```
User selects:                        Pipeline:
  - Index type (Exchange Rate,       → PriceCalculator.calculate()
    PPP, BigMac, Netflix…)              OR FX conversion via api.overx.ai
  - Base price ($9.99 USD)           → apply_vat() (optional)
  - Base territory (US)              → apply_currency_rounding() (smart)
  - VAT toggle (on/off)                OR apply_charming() (.99/.95)
  - Charming mode (Smart/None/.99)   → nearest Apple price point
                                     → Preview result
```

### PriceCalculator ABC

**File**: `backend/app/services/pricing/calculator.py`

All calculators implement:
```python
def calculate(self, base_price: Decimal, index_value: Decimal, base_index_value: Decimal) -> Decimal
```

| Calculator | File | Formula |
|-----------|------|---------|
| **Exchange Rate** | `exchange_rate.py` | `base_price × exchange_rate` (live FX from api.overx.ai) |
| PPP | `ppp.py` | `base_price × (territory_ppp / base_ppp)` |
| Big Mac | `bigmac.py` | `base_price × (territory_bigmac / base_bigmac)` |
| Netflix | `netflix.py` | `base_price × (territory_netflix / base_netflix)` |
| Spotify | `spotify.py` | `base_price × (territory_spotify / base_spotify)` |
| Fixed Payout | `fixed_payout.py` | `target_payout / (1 - commission_rate)` |

PPP, BigMac, Netflix, Spotify, and Exchange Rate all inherit `ProportionalCalculator` (DRY shared base).

**File**: `backend/app/services/pricing/engine.py`

### PriceEngine

The single entry point for price calculation:
1. Picks the appropriate `PriceCalculator`
2. Optionally applies VAT: `price × (1 + vat_rate)`
3. Applies rounding: Smart (currency-aware) or Charming (.99 / .95)

## Exchange Rate Mode

**File**: `backend/app/services/pricing/exchange_rate.py`
**File**: `backend/app/services/rates/client.py`

When `index_type == "exchange_rate"`, the preview endpoint:
1. Fetches live FX rates from `api.overx.ai` (`GET /api/v1/rates?base=USD`, 166 currencies, no auth)
2. Converts: `raw_price = base_price × exchange_rate`
3. Applies smart currency rounding (see below)
4. Finds nearest Apple price point

This mode bypasses the `EconomicIndex` table entirely — rates are fetched live each time.

## GDP-Bracket Pricing Mode

**File**: `backend/app/services/pricing/gdp_brackets.py`
**Spec**: [005 - GDP-Bracket Pricing](specs/005-gdp-bracket-pricing.md)

`index_type == "gdp_brackets"` is a third dispatch path (alongside `PriceCalculator` and Exchange Rate mode) that assigns each territory an **absolute** USD tier price instead of scaling proportionally from a base price. `assign_tier(territory_code, gdp_value, config)` resolves one of four tiers in priority order:

1. Territory in `special_territories` → `special`
2. Territory in `manual_overrides` → that tier
3. `gdp_value >= tier_thresholds_usd.top_min` → `top`; `>= mid_min` → `mid`
4. Otherwise (including missing GDP data) → `low`

Each tier maps to a fixed `tier_prices_usd` value from the request's `GDPBracketConfig` (`backend/app/schemas/pricing.py`). From there the tier price runs through the same FX conversion, smart currency rounding, Apple price-point matching, and `SAFETY_BAND_PCT` safety check (±50%) as every other strategy — see `_gdp_bracket_items()` in `backend/app/services/pricing/preview.py`.

GDP/capita PPP values are sourced from World Bank `NY.GDP.PCAP.PP.CD` via `GDPFetcher` (stored **raw**, not US-normalized — see Economic Indices below) and browsed via `GET /api/v1/indices/gdp`. `GDPBracketConfig` (tier prices, thresholds, overrides, special list) is persisted per preset in `PricePreset.config` (nullable JSON — see Price Presets below).

## Smart Currency Rounding

**File**: `backend/app/services/pricing/currency_rounding.py`

The `apply_currency_rounding()` function rounds prices to "nice" values appropriate for each currency, with **±10% flexibility** from the raw converted price.

### Currency Profiles (50+ currencies)

| Category | Currencies | Strategy | Examples |
|----------|-----------|----------|---------|
| Standard .99 | USD, EUR, GBP, AUD, CAD, CHF, etc. | `floor + 0.99` | 9.99, 14.99, 29.99 |
| ARS | ARS | Tiered steps + .99 | 49.99, 349.99, 4099.99 |
| JPY, TWD | JPY, TWD | Round to 10s | 990, 1490, 2540 |
| KRW, CLP, COP | KRW, CLP, COP | Round to 100s | 9900, 14900, 24000 |
| VND, IDR | VND, IDR | Round to 1000s | 249000, 418000 |
| INR, PKR | INR, PKR, BDT, LKR | Step 100, suffix -1 | 799, 899, 1499 |
| BRL | BRL | `floor + 0.90` | 9.90, 52.90 |
| RUB | RUB | `floor + 0.00` | 299.00, 884.00 |
| HUF, ISK | HUF, ISK | Round to 10s | 3990, 4050 |
| PHP, THB | PHP, THB | Round to 10s, suffix -1 | 469, 589 |

### ±10% Flexibility Algorithm

1. Generate candidate "nice" prices around the raw converted value
2. Filter to candidates within ±10% of the raw price
3. Pick the closest candidate to the raw price

This allows €14.71 to round UP to €14.99 (within 10%), or ¥1493 to land on ¥1490.

### Magnitude-Aware Tiers

Zero-decimal currencies adapt rounding step to price magnitude:
- JPY < 10000 → round to 10s; JPY >= 10000 → round to 100s
- KRW < 100000 → round to 100s; KRW >= 100000 → round to 1000s
- INR < 1000 → step 100; INR 1000-10000 → step 500

### Apple Price Points

Apple does not allow arbitrary prices — each territory has a fixed set of allowed price points ($0.99, $1.99, $2.99…). The price resolver finds the nearest available Apple price point for any calculated price.

Price points are **not** bulk-fetched (~140k records, 20+ minutes). Instead, they're looked up per-territory on demand during the apply operation. See [002 - ASC Integration](002-asc-integration.md) for the cache-first architecture.

### Pricing Data Flow

```
Page Load:
  GET .../prices         → Read from subscription_prices DB table (instant)
  
Preview:
  POST .../prices/preview → Exchange rates from api.overx.ai + DB cache (instant)

Sync (explicit user action):
  POST .../sync          → Fetch ~175 current prices from ASC API (~2.5s)
                           Store in subscription_prices table
```

## Economic Indices

### Data Sources

| Index | Fetcher | Source | Notes |
|-------|---------|--------|-------|
| PPP | `indices/ppp.py` | World Bank API | Annual update |
| Big Mac | `indices/bigmac.py` | Economist GitHub CSV | Semi-annual |
| Netflix | `indices/netflix.py` | Seed data | ~70 countries, manual quarterly update |
| Spotify | `indices/spotify.py` | Seed data | ~70 countries, manual quarterly update |
| GDP/capita PPP | `indices/gdp.py` | World Bank API (`NY.GDP.PCAP.PP.CD`) | Annual update; stores **raw** value (not US-normalized) — drives GDP-bracket pricing, not `ProportionalCalculator` |

### IndexFetcher ABC

**File**: `backend/app/services/indices/base.py`

Returns `list[IndexRecord]` — territory_code, value (multiplier relative to US=1.0), reference_date.

### IndexRefreshService

**File**: `backend/app/services/indices/refresh.py`

Orchestrates all fetchers, upserts `EconomicIndex` records by (territory_id, index_type).

## Price Presets

Users can save pricing configurations (index type, base price, territory, VAT, charming) as named presets for quick reuse.

**File**: `backend/app/api/v1/presets.py`

**Model**: `backend/app/models/preset.py` — `PricePreset`, with a nullable `config: dict | None` JSON column for strategies needing extra state beyond the base fields (currently just `GDPBracketConfig` for `gdp_brackets`; older rows have `config=NULL` and are unaffected).

## Export / Import

**File**: `backend/app/services/export/excel.py` — `ExcelExportService`
**File**: `backend/app/services/export/csv.py` — `CSVExportService`

Both implement `export_prices(subscription_name, prices) → bytes` and `import_prices(file_bytes) → list[dict]`.

## Price Point Filesystem Cache

**File**: `backend/app/services/asc/price_point_cache.py`

Apple price points are discrete (~40-80 per territory). Bulk-fetching all ~140k records takes 20+ minutes, so we cache per-territory JSON files on the filesystem.

### Storage

```
backend/.cache/price_points/{product_asc_id}/{alpha2}.json
```

Each file (~5 KB) contains the full list of price points for one territory. Supports both subscriptions and IAPs via the `product_type` parameter.

### Cache Operations

| Method | Purpose |
|--------|---------|
| `sync(pricing_svc, concurrency=2)` | Fetch all 175 territories from ASC, store as JSON files |
| `get(territory_code)` | Read cached price points for a territory (async via `asyncio.to_thread`) |
| `status()` | Return `{cached_territories, synced_at}` |
| `clear()` | Delete entire cache directory for this product |

### Security

Path segments are validated with `^[A-Za-z0-9_-]+$` regex to prevent directory traversal.

## Price Safety Limits

When applying prices, each territory is checked against current prices:

| Direction | Threshold | Behavior |
|-----------|-----------|----------|
| Price increase | > +20% | Territory skipped |
| Price decrease | > -25% | Territory skipped |

The safety check uses `nearest_apple_price` (or `suggested_price` as fallback when no Apple price tier is cached). Skipped territories are reported in the apply response with reason, current price, new price, and diff percent.

**File**: `backend/app/api/v1/pricing.py` — `apply_subscription_prices()`, `apply_iap_prices()`

## Manual Price Pins

Users can pin specific territories for manual price management. Pinned territories are excluded from bulk price calculations and instead use a per-territory price entered by the user.

### Flow

1. User toggles pin on a territory row in the PriceGrid
2. User enters a custom price for that territory
3. Frontend calls `POST .../prices/resolve` with `{territory_code, customer_price}`
4. Backend finds the nearest Apple price point matching the requested price
5. Manual items are merged into the apply request alongside calculated prices

**File**: `backend/app/api/v1/pricing.py` — `resolve_manual_price()`, `resolve_iap_manual_price()`
**File**: `frontend/src/components/pricing/PriceGrid.tsx` — pin toggle column, editable `NumberInput`

## IAP Pricing Workflow

IAPs share the same pricing workflow as subscriptions: sync, preview, apply, manual pins. The price engine, safety limits, and frontend components (`PriceGrid`, `PriceMultiplierPanel`, `ExportImportButtons`) are reused.

### Key Differences

| Aspect | Subscriptions | IAPs |
|--------|--------------|------|
| Price write API | `POST /v1/subscriptionPrices` (per-territory) | `POST /v1/inAppPurchasePriceSchedules` (batch) |
| Price read API | v1 nested relationship | v2 with base64 ID decoding |
| Price points API | v1 | v2 |
| ASC resource type | `subscriptionPricePoints` | `inAppPurchasePricePoints` |

### IAP Price Schedule Decoding

IAP price IDs are base64-encoded and contain `{s: iap_id, t: territory_alpha3, p: price_point_num}`. The service decodes these, matches against paginated v2 price points to resolve actual `customer_price` and `proceeds`.

**File**: `backend/app/services/asc/pricing.py` — `get_iap_price_schedule()`

## API Endpoints

### Subscription Pricing

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `.../subscriptions` | List groups + subscriptions (syncs from ASC) |
| `GET` | `.../subscriptions/{sub_id}/prices` | Read current prices from DB |
| `POST` | `.../subscriptions/{sub_id}/sync` | Sync prices from ASC |
| `POST` | `.../subscriptions/{sub_id}/price-points/sync` | Cache Apple price tiers to filesystem |
| `GET` | `.../subscriptions/{sub_id}/price-points/status` | Cache status (territory count, sync date) |
| `POST` | `.../subscriptions/{sub_id}/prices/preview` | Calculate suggested prices |
| `POST` | `.../subscriptions/{sub_id}/prices/resolve` | Find nearest Apple tier for manual price |
| `POST` | `.../subscriptions/{sub_id}/prices/apply` | Apply price changes to ASC |

### IAP Pricing

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `.../iaps` | List IAPs (syncs from ASC) |
| `GET` | `.../iaps/{iap_id}/prices` | Read current IAP prices from DB |
| `POST` | `.../iaps/{iap_id}/sync` | Sync IAP prices from ASC |
| `POST` | `.../iaps/{iap_id}/price-points/sync` | Cache IAP price tiers |
| `GET` | `.../iaps/{iap_id}/price-points/status` | Cache status |
| `POST` | `.../iaps/{iap_id}/prices/preview` | Calculate suggested IAP prices |
| `POST` | `.../iaps/{iap_id}/prices/resolve` | Find nearest tier for manual IAP price |
| `POST` | `.../iaps/{iap_id}/prices/apply` | Apply IAP price changes to ASC |

### Other

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/prices/export` | Download prices as Excel/CSV |
| `POST` | `/api/v1/prices/import` | Upload and parse Excel/CSV prices |
| `CRUD` | `/api/v1/presets` | Manage saved pricing presets |
| `GET` | `/api/v1/territories` | All 202 territories |
| `GET` | `/api/v1/indices/status` | Last refresh timestamps per index |
| `POST` | `/api/v1/indices/refresh` | Trigger economic index refresh |

## Frontend Components

**File**: `frontend/src/pages/PricingPage.tsx` — Main page with tabs (Subscriptions / IAPs / Localizations)
**File**: `frontend/src/components/pricing/PriceGrid.tsx` — mantine-datatable, 175 rows, filterable/sortable, manual pin toggle
**File**: `frontend/src/components/pricing/PriceMultiplierPanel.tsx` — Controls (index, base price, VAT, charming), skipped territory alerts
**File**: `frontend/src/components/pricing/PriceDiffBadge.tsx` — Color-coded diff badge
**File**: `frontend/src/components/pricing/PresetManager.tsx` — Save/load presets inline
**File**: `frontend/src/components/pricing/ExportImportButtons.tsx` — Download/upload buttons
**File**: `frontend/src/components/pricing/LocalizationEditor.tsx` — Inline localization editor with JSON import
**File**: `frontend/src/components/pricing/ReviewScreenshotUpload.tsx` — Screenshot upload/preview for subs and IAPs

### PriceGrid Row States

| Color | Meaning |
|-------|---------|
| Blue highlight | Manual (pinned) territory |
| Orange | Skipped (exceeds safety limits) |
| Yellow | Changed (new price differs from current) |

## Important: Apple Price Point Constraint

Apple prices are **not arbitrary** — each territory has ~40-80 discrete price points. The preview endpoint reads from the filesystem cache and returns:
- `suggested_price`: the calculated ideal price
- `nearest_apple_price`: the closest available Apple price point
- `price_point_id`: the ID to use when applying
- `would_be_skipped`: whether safety limits would exclude this territory

Always use `price_point_id` (not `suggested_price`) when calling the apply endpoint.
