# ASO-Light

App Store Optimization SaaS — web-based alternative to aso.dev. Focuses on price management across locales and keyword analysis.

## Tech Stack

- **Backend**: FastAPI (Python 3.12+), SQLAlchemy 2.0 async, Alembic, pydantic-settings, uv
- **Database**: SQLite (dev via `sqlite+aiosqlite`) / PostgreSQL (prod via `asyncpg`)
- **Auth**: JWT HS256 (python-jose), bcrypt direct (NOT passlib — version conflict), Fernet for .p8 key encryption
- **ASC API auth**: ES256 JWT via PyJWT — NOT python-jose (different signing interface needed)
- **HTTP client**: httpx (async)
- **Exchange rates**: rate-cache-api at `api.overx.ai` (166 currencies, no auth, hourly cache)
- **Frontend**: React 19 + TypeScript, Vite, Mantine v8, TanStack Query v5, react-router-dom v7, mantine-datatable, @mantine/charts
- **Package managers**: `uv` (backend), `npm` (frontend)

## Critical Conventions

- **Python imports**: always absolute (`from app.core.config import settings`, never `from .config import settings`)
- **Price math**: use `Decimal` throughout — never `float` for monetary calculations
- **SQLAlchemy**: use `select()` + `session.execute()` style only — never legacy `.query()`
- **ASC ownership**: always verify `app.credential_id → credential.user_id == current_user_id` before any ASC operation
- **Error messages**: never expose raw Python errors to API responses; use `HTTPException`
- **Model style**: SQLAlchemy 2.0 mapped_column with `Mapped[type]` annotations
- **FastAPI**: `redirect_slashes=False` — route paths use `""` not `"/"` for root endpoints
- **httpx**: use `aclose()` not `close()` on `AsyncClient`
- **ASC territory codes**: ASC API returns alpha-3 (ARE, USA); our DB uses alpha-2 (AE, US) — use `ALPHA2_TO_ALPHA3` from `app/data/territories.py`
- **Pricing endpoints**: read from DB cache; ASC sync is explicit via `POST .../sync`
- **ASC binary uploads**: use `_put_binary()` (no auth headers) for pre-signed S3 URLs — Apple rejects Bearer tokens on those

## Best Practices

- Encrypt `.p8` keys at rest with Fernet; decrypt in memory only for the duration of an API call
- Use `PriceCalculator` ABC when adding new index types — never add ad-hoc calculation logic in routes
- Use `IndexFetcher` ABC when adding new economic data sources
- Use `apply_currency_rounding()` for local-currency rounding — never hardcode `.99` for non-USD currencies
- Add new currency profiles in `currency_rounding.py` when supporting new currencies with special rounding
- Use `_get_all_pages()` from `ASCClient` for any paginated ASC endpoint
- Frontend API calls always go through TanStack Query hooks in `src/lib/hooks.ts`

## Workflows

- **Start dev**: `make dev` (starts backend on :8000, frontend on :5173)
- **DB init**: `make db-up && make migrate` (PostgreSQL) or just `make dev` (SQLite auto-creates)
- **Run backend alone**: `cd backend && uv run uvicorn app.main:app --reload --port 8000`
- **Run frontend alone**: `cd frontend && npm run dev`
- **Generate Fernet key**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Architecture Decisions

- `bcrypt` used directly (not passlib) — passlib has a version conflict with bcrypt 4.x
- `PyJWT` used for ASC tokens, `python-jose` used for app auth — do NOT mix them
- `ProportionalCalculator` base class is shared by PPP/BigMac/Netflix/Spotify/ExchangeRate (all use same formula)
- Exchange rate mode fetches live FX rates from `api.overx.ai` — bypasses EconomicIndex table entirely
- Smart currency rounding uses ±10% flex to find the "nicest" local price near the raw converted value
- `ASCClient.from_credential()` is the only place private keys are decrypted
- `seed_territories()` is idempotent — safe to call on every startup
- **IAP endpoints require ASC API v2**: localizations (`?include=inAppPurchaseLocalizations`), price schedule (`iapPriceSchedule`), and price points all use `/v2/inAppPurchases/{id}/...`; create/update mutations still use v1 top-level resources
- **ASC rate limiter**: 150ms min interval between requests, global backoff on 429, max 6 retries
- **Price point cache**: filesystem-based under `backend/.cache/price_points/`, not DB — per-territory JSON files
- **Price safety limits**: ±50% — skip territory if price change exceeds this threshold in either direction (constant `SAFETY_BAND_PCT` in `backend/app/api/v1/pricing.py`)

## Project-Specific Rules

- All 202 territories are seeded on startup via `app/data/seed.py` (idempotent)
- Economic indices start empty — call `POST /api/v1/indices/refresh` to populate
- Apple price points are discrete (not arbitrary) — always use `price_point_id` from preview when applying prices
- Cross-localization data is static Python (not DB) — edit `app/services/keywords/cross_localization.py` to update
- Netflix/Spotify indices are seed data — update `app/services/indices/netflix.py` and `spotify.py` quarterly
- `RATE_CACHE_API_URL` defaults to `https://api.overx.ai` — the rate-cache-api from `1B-bots/rate-cache-api`
- Currency rounding profiles are in `app/services/pricing/currency_rounding.py` — update when App Store adds new territories
- **Subscription immutables**: `productId` and `subscriptionPeriod` are immutable in ASC after create — never expose them on update paths (`SubscriptionUpdate` schema enforces this)
- **Intro offers**: `FREE_TRIAL` rejects `price_point_id`; `PAY_AS_YOU_GO` / `PAY_UP_FRONT` require it; `FREE_TRIAL` and `PAY_UP_FRONT` force `number_of_periods = 1` (validated in `IntroOfferCreate`); `IntroOfferDuration` extends `SubscriptionPeriod` with `THREE_DAYS` and `TWO_WEEKS`
- Submit-for-review is **not** automated — subscription state transitions are done manually in App Store Connect
- **AI translation is suggestion-only**: `AnthropicTranslator` (and any future `AbstractTranslator` impl) returns text for the user to review — the metadata router never auto-applies translations. Per-app monthly soft cap is 500 calls (rolling 30 days, cached); raise via `MetadataTranslationCache` table.
- **Metadata version state machine**: when `editable_version_state == 'READY_FOR_DISTRIBUTION'`, only `promotional_text` is mutable on `appStoreVersionLocalizations`. The `editable_fields` list returned by `GET /apps/{id}/metadata` is the source of truth for the UI.

## Modes
When I switch to plan mode (shift+tab), you MUST only outline steps and reasoning.
Do NOT execute any tools in plan mode. Wait for me to switch back to act mode before executing.

## See Also

- [docs/000-architecture.md](docs/000-architecture.md) — Full system architecture
- [docs/001-pricing-system.md](docs/001-pricing-system.md) — Pricing system deep dive
- [docs/002-asc-integration.md](docs/002-asc-integration.md) — ASC API integration details
- [docs/003-keyword-analysis.md](docs/003-keyword-analysis.md) — Keyword analysis system
- [docs/004-localization-management.md](docs/004-localization-management.md) — Localization management
- [docs/005-subscription-management.md](docs/005-subscription-management.md) — Subscription / group / intro-offer write paths
