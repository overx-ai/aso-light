# ASO-Light

Web-based App Store Optimization SaaS — a focused alternative to aso.dev. Manage iOS App Store prices across 175+ territories using economic multipliers (PPP, Big Mac, Netflix, Spotify, exchange rate) and track keyword performance.

## Features

- **Smart pricing across territories** — apply Purchasing Power Parity, Big Mac Index, Netflix/Spotify benchmarks, or live exchange rates to derive locale-appropriate prices from a USD baseline
- **Currency-aware rounding** — locale-specific charm pricing (e.g., `.99` in USD, `99` in JPY, `990` in HUF) within ±10% flex
- **App Store Connect integration** — preview, sync, and apply price schedules and IAP prices via the ASC API (v1 + v2), with rate limiting and retry
- **Price point cache** — filesystem-cached ASC price points per territory to avoid re-fetching Apple's discrete price ladders
- **Safety limits** — guardrails on price changes (+20% up / −25% down) to prevent runaway updates
- **Keyword analysis** — rank tracking, iTunes search suggestions, cross-localization matrix
- **Localization management** — subscription and IAP display names and descriptions, bulk sync, JSON import/export
- **Presets** — save and reuse pricing strategies across apps

### Metadata Editor + AI Translation (Phase 5)

Edit App Store metadata (title, subtitle, keywords, description, promotional text, what's new) per locale without leaving ASO-Light. Bulk fan-out a field across N locales with diff preview. Optional one-click translation via Anthropic Claude.

- Set `ANTHROPIC_API_KEY` in `backend/.env` to enable AI translation suggestions.
- Translations are SUGGESTIONS only — never auto-applied. You always confirm before saving.
- Per-app cap: 500 translations / 30 days (cached, so re-translating the same source is free).
- The Cross-Localization grid surfaces Apple's secondary-indexing pattern (e.g. es-MX content surfaces in US/BR/AR/CL/CO/PE App Stores).

See `docs/specs/007-metadata-editor-and-cross-loc.md` for the full design.

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | FastAPI · Python 3.12+ · SQLAlchemy 2.0 (async) · Alembic · `uv` |
| Database | SQLite (dev) · PostgreSQL (prod, `asyncpg`) |
| Auth | JWT HS256 (app) · ES256 JWT via PyJWT (ASC) · Fernet at-rest for `.p8` keys |
| Frontend | React 19 · TypeScript · Vite · Mantine v8 · TanStack Query v5 · react-router v7 |
| External | App Store Connect API · `api.overx.ai` rate cache (166 currencies) |

## Quick Start

```bash
# Backend + frontend dev servers
make dev          # backend :8000, frontend :5173

# Or run individually
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev

# Apply migrations manually (optional preflight for any database)
make db-up && make migrate

# Generate a Fernet key for FERNET_KEY in backend/.env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy `backend/.env.example` to `backend/.env` and fill in `SECRET_KEY`, `JWT_SECRET_KEY`, and `FERNET_KEY`.

Backend startup is migration-first in every environment: it runs Alembic
`upgrade head` before seeding territories. `make migrate` remains available
when you want an explicit preflight step before starting the app.

## Project Structure

```
backend/
  app/
    api/v1/        FastAPI routes (auth, apps, pricing, keywords, presets, ...)
    core/          Config, security
    data/          Territory seed (202 territories, alpha-2/alpha-3 mapping)
    db/            Async session
    models/        SQLAlchemy 2.0 models
    schemas/       Pydantic schemas
    services/
      asc/         App Store Connect client, pricing, price point cache
      pricing/     Calculators (PPP, BigMac, Netflix, Spotify, exchange rate, ...)
      keywords/    Tracker, suggestions, cross-localization
      indices/     Economic index fetchers
      rates/       Exchange rate client (api.overx.ai)
      export/      CSV/Excel export
  alembic/         Migrations
  tests/
frontend/
  src/
    components/    Pricing grid, keywords, localization editor, etc.
    pages/         Login, dashboard, pricing, keywords, settings, ...
    lib/           API client, TanStack Query hooks, auth context
docs/              Architecture and feature documentation
```

## Documentation

| Doc | Topic |
|-----|-------|
| [Architecture](docs/000-architecture.md) | System overview, components, design decisions |
| [Pricing System](docs/001-pricing-system.md) | Calculators, exchange rates, rounding, safety limits, presets |
| [ASC Integration](docs/002-asc-integration.md) | App Store Connect API (v1+v2), JWT, rate limiter |
| [Keyword Analysis](docs/003-keyword-analysis.md) | Ranking tracker, cross-localization, suggestions |
| [Localization Management](docs/004-localization-management.md) | Subscription/IAP display names, bulk sync |

## Status

Pre-release. APIs and schemas may change.
