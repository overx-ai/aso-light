# 000 - Changelog

## [Unreleased]

### Added
- **Subscription management write-paths**: Create / rename subscription groups, create / update subscriptions, CRUD group localizations, list / create / delete introductory offers — all driven from the Subscriptions tab via 4 new modals. Submit-for-review remains manual.
- **GDP-bracket pricing strategy** (spec 005): 4 absolute-price tiers (top / mid / low / special) with World Bank GDP/capita PPP data and per-preset config (`PricePreset.config` JSON column). Loosens price safety band to symmetric ±50%.
- **App availability management page**: Per-territory availability editor backed by `subscriptionAvailability` ASC API.
- **IAP full pricing workflow**: Sync, preview, apply, manual pins for in-app purchases — mirrors subscription pricing with shared components
- **Manual price pins**: Pin territories for manual price management, resolve to nearest Apple price tier per-territory
- **Review screenshot upload**: 3-step upload flow (reserve → PUT binary → commit) for both subscriptions and IAPs
- **Localization management**: CRUD + bulk sync for subscription and IAP display names/descriptions across locales
- **Localizations tab** in Pricing page with inline editing, JSON import, and character limit validation (30 name / 55 description)
- **Price point filesystem cache**: Per-territory JSON cache under `backend/.cache/price_points/` with sync button and cache status display
- **Price safety checks**: Skip territories where price change exceeds +20% up or -25% down (protects against bad FX data)
- **ASC rate limiter**: Global 150ms throttle between requests + exponential backoff on 429 (up to 6 retries)
- **Sync Price Tiers** button with cache status badge (territory count + sync date tooltip)
- **Exchange Rate pricing mode**: Converts USD base price to local currencies using live FX rates from rate-cache-api (`api.overx.ai`), with smart currency-aware rounding (±10% flex)
- **Smart currency rounding**: 50+ currency profiles — `.99` for USD/EUR/GBP, ¥1490 for JPY, ₩14900 for KRW, ₹799 for INR, R$52.90 for BRL, etc.
- **Rate-cache-api client**: `backend/app/services/rates/client.py` — async httpx client for exchange rate fetching (166 currencies)
- **"Smart" charming mode**: Currency-aware rounding available for all index types via `charming_mode="smart"`
- `RATE_CACHE_API_URL` config setting (defaults to `https://api.overx.ai`)
- **Cache-first pricing architecture**: Prices and preview read from DB cache — no ASC API calls on page load
- **Sync from Apple endpoint**: `POST /apps/{id}/subscriptions/{sub_id}/sync` — fetches ~175 current prices from ASC in ~2.5s, stores in DB
- **Sync from Apple button**: Frontend button to trigger explicit price sync from ASC
- **ARS currency rounding profile**: Tiered rounding for Argentine Peso (49.99, 349.99, 4099.99)
- **Territory mapping extracted**: `ALPHA2_TO_ALPHA3` dict moved to `backend/app/data/territories.py` (shared across services)

### Changed
- `redirect_slashes=False` on FastAPI app — prevents 307 redirects that strip auth headers
- All root route paths changed from `"/"` to `""` (apps, credentials, territories)
- Credential `POST` endpoint uses `Form()` + `File()` annotations for multipart upload
- `httpx.AsyncClient.aclose()` used instead of `.close()` in ASCClient
- Removed `platform` from ASC `fields[apps]` query (Apple deprecated the field)
- PriceGrid falls back to `suggested_price` when `nearest_apple_price` is null
- Charming Price selector no longer locked to "Smart" for Exchange Rate mode
- IAP price schedule uses v2 API (`/v2/inAppPurchases/{id}/iapPriceSchedule`) — v1 path doesn't exist for v2 IAPs
- ASCClient `_MAX_RETRIES` increased from 3 to 6
- PricePointCache concurrency reduced from 5 to 2 (avoids rate limit hits)

### Fixed
- Login redirect loop: 307 slash redirect stripped Authorization header → 401 → token cleared
- Credential creation crash: async lazy-load of `credential.apps` relationship
- Territory code mismatch: frontend defaulted to `"USA"` but DB uses `"US"`
- ASC API `platform` field rejection on app sync
- Vite proxy target: port 8002 → 8000
- Indices refresh notification: response shape `{refreshed: {...}}` not flat object
- Charming mode mismatch: frontend sends `.99`/`.95`, backend matched `99`/`95`
- Duplicate territories in preview: `_unique_territories()` helper added
- IAP localizations 404: switched to v2 API for listing
- Screenshot upload auth conflict: separate httpx client without Bearer token for S3 PUT
- Screenshot stuck AWAITING_UPLOAD: delete existing before creating new
- IAP price schedule v1 404: rewrote to v2 with base64 ID decoding + pagination
- Safety check downward: added -25% threshold alongside +20% up
- Safety check without tiers: use `suggested_price` fallback when `nearest_apple_price` is null
- Price point cache sync I/O: moved to async via `asyncio.to_thread()`
- Path traversal: `_validate_path_segment()` regex guard on cache paths

---

## [0.1.0] - 2026-03-23

### Added
- **User auth**: Register, login, JWT access+refresh tokens (HS256)
- **ASC credentials**: Upload `.p8` keys (Fernet-encrypted at rest), test connection
- **App sync**: Fetch and store apps from App Store Connect API
- **Territory database**: 202 territories seeded with currency codes and VAT rates
- **Price management**:
  - Read subscription and IAP prices from ASC API (175 territories)
  - Price calculators: PPP, Big Mac, Netflix, Spotify, Fixed Payout
  - VAT application per territory
  - Charming price rounding (.99 / .95 / none)
  - Price preview (calculate without applying)
  - Price apply (bulk push to ASC API)
  - Price presets (save/load pricing configurations)
  - Excel (.xlsx) and CSV export/import
- **Economic indices**:
  - PPP fetcher via World Bank API
  - Big Mac Index fetcher via Economist GitHub CSV
  - Netflix/Spotify seed data (~70 countries each)
  - Index refresh service with status tracking
- **Keyword analysis**:
  - Keyword suggestions via iTunes Search Hints API
  - Keyword rank tracking via iTunes Search API
  - Cross-localization matrix (114 locale→territory mappings)
  - Competitor app tracking and keyword analysis
- **Frontend**:
  - Auth pages (login/register)
  - ASC credential management with .p8 upload
  - App dashboard with sync
  - Price grid (175 rows, sortable/filterable, diff visualization)
  - Price multiplier panel (index selector, VAT toggle, charming mode)
  - Preset manager (save/load configurations)
  - Export/import buttons
  - Keyword tracking table with rank history charts
  - Cross-localization matrix UI
  - Competitor analysis
  - Settings page with index freshness indicators
