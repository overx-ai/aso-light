# ASO-Light

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A self-hosted App Store Optimization tool — a focused, open alternative to aso.dev. Manage iOS App Store prices across 175+ territories, edit metadata per locale, track keywords, triage reviews, and analyse Apple Search Ads — all from one web app that talks directly to App Store Connect.

> **Status:** Pre-release. APIs and schemas may change.

## Features

### Pricing
- **Smart pricing across territories** — apply Purchasing Power Parity, Big Mac Index, Netflix/Spotify benchmarks, or live exchange rates to derive locale-appropriate prices from a USD baseline
- **Currency-aware rounding** — locale-specific charm pricing (e.g. `.99` in USD, `¥1490` in JPY, `₩14900` in KRW) within ±10% flex
- **App Store Connect integration** — preview, sync, and apply price schedules and IAP prices via the ASC API (v1 + v2), with rate limiting and retry
- **Price point cache** — filesystem-cached ASC price points per territory to avoid re-fetching Apple's discrete price ladders
- **Safety limits** — guardrails on price changes (±50%) to prevent runaway updates
- **Presets** — save and reuse pricing strategies; CSV/Excel export/import

### Metadata & Localization
- **Metadata editor** — edit title, subtitle, keywords, description, promotional text, and what's-new per locale
- **Bulk fan-out** — broadcast one field across N locales with diff preview, with same value or per-locale translated values
- **AI translation** (optional) — one-click Claude-backed translation suggestions (never auto-applied)
- **Cross-localization grid** — surfaces Apple's secondary-indexing pattern (e.g. `es-MX` content surfaces in US/BR/AR/CL/CO/PE)
- **Localization management** — subscription and IAP display names and descriptions, bulk sync, JSON import/export

### Keywords & Growth
- **Keyword analysis** — rank tracking, iTunes search suggestions, cross-localization matrix
- **Keyword intelligence** — pluggable volume + difficulty scoring (free ASA-derived providers included)
- **Growth recommendations** — cross-domain advisor that surfaces prioritised actions from metadata, keyword, paid-search, and review signals

### Reviews & Paid Search
- **Reviews** — read, theme-classify (LLM-tagged bug/feature/praise/…), priority-sort a reply queue, and draft + translate replies
- **Apple Search Ads analytics** — ingest campaign/keyword/search-term metrics, KPI dashboard, paid-organic keyword join, negative-keyword suggestions

### Automation
- **MCP server** — exposes the full REST surface (~120 tools) to LLM clients like Claude Code via Personal Access Tokens

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | FastAPI · Python 3.12+ · SQLAlchemy 2.0 (async) · Alembic · `uv` |
| Database | SQLite (dev) · PostgreSQL (prod, `asyncpg`) |
| Auth | JWT HS256 (app) · ES256 JWT via PyJWT (ASC + ASA) · Fernet at-rest for `.p8` keys |
| Frontend | React 19 · TypeScript · Vite · Mantine v8 · TanStack Query v5 · react-router v7 |
| External | App Store Connect API · Apple Search Ads API · [`api.overx.ai`](https://github.com/overx-ai/rate-cache-api) free public rate cache (166 currencies) |
| AI (optional) | Anthropic Claude (`claude-haiku-4-5`) for translation, review themes, reply drafts |

## Quick Start

```bash
git clone https://github.com/overx-ai/aso-light.git
cd aso-light

# Install backend + frontend dependencies
make install

# Apply migrations manually (optional preflight for any database)
make db-up && make migrate

# Generate a Fernet key for FERNET_KEY in backend/.env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Start backend (:8002) + frontend (:5173)
make dev
```

Open <http://localhost:5173>. SQLite auto-creates on first run — no database setup needed for development.

### Optional features

- **AI translation / review themes** — set `ANTHROPIC_API_KEY` in `backend/.env` (get one from [console.anthropic.com](https://console.anthropic.com)).
- **Apple Search Ads** — add ASA credentials (clientId, teamId, `.p8` key) in **Settings**; Paid Search features unlock once a credential is saved.

## Self-Hosting

ASO-Light is a standard FastAPI + React + PostgreSQL app and runs anywhere you can run those.

```bash
# 1. Start PostgreSQL (Docker Compose included for convenience)
make db-up

# 2. Apply migrations
make migrate

# 3. Set production environment in backend/.env
#    - DATABASE_URL  → your Postgres connection string
#    - SECRET_KEY / JWT_SECRET_KEY → long random strings
#    - FERNET_KEY    → generated key (see Quick Start) — back this up; losing it
#                      makes stored .p8 credentials unrecoverable
#    - CORS_ORIGINS  → your frontend origin(s)

# 4. Serve the backend behind a reverse proxy (nginx/Caddy) with TLS
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8002

# 5. Build and serve the frontend
cd frontend && npm run build   # output in frontend/dist/
```

The MCP server is mounted at `/mcp` on the same FastAPI app and authenticates with Personal Access Tokens — see [docs/007-mcp-integration.md](docs/007-mcp-integration.md).

Backend startup is migration-first in every environment: it runs Alembic
`upgrade head` before seeding territories. `make migrate` remains available
when you want an explicit preflight step before starting the app.

## Project Structure

```
backend/
  app/
    api/v1/        FastAPI routes (auth, apps, pricing, keywords, metadata, reviews, asa, …)
    core/          Config, security
    data/          Territory seed (202 territories, alpha-2/alpha-3 mapping)
    db/            Async session
    models/        SQLAlchemy 2.0 models
    schemas/       Pydantic schemas
    services/
      asc/         App Store Connect client, pricing, price point cache
      asa/         Apple Search Ads client, sync, paid-organic joins
      pricing/     Calculators (PPP, BigMac, Netflix, Spotify, exchange rate, …)
      keywords/    Tracker, suggestions, cross-localization
      keyword_intel/ Volume + difficulty providers
      metadata/    Metadata snapshot, bulk fan-out, translation
      reviews/     Theme classifier, reply drafting
      growth/      Cross-domain recommendation engine
      indices/     Economic index fetchers
      rates/       Exchange rate client
    mcp/           MCP server, tools, PAT auth
  alembic/         Migrations
  tests/
frontend/
  src/
    components/    Pricing grid, keywords, metadata editor, reviews, …
    pages/         Login, dashboard, pricing, keywords, metadata, reviews, paid search, growth, settings
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
| [Subscription Management](docs/005-subscription-management.md) | Groups, subscriptions, intro offers |
| [Metadata Editor](docs/006-metadata-editor.md) | Per-locale metadata, bulk fan-out, AI translation, cross-loc grid |
| [Product Swap](docs/006-product-swap-ios-integration.md) | Subscription/IAP swap flow + iOS checklist |
| [MCP Integration](docs/007-mcp-integration.md) | MCP server, Personal Access Tokens, tool reference |
| [Reviews Theme Classifier](docs/009-reviews-theme-classifier.md) | LLM-tagged review themes + reply queue |
| [Keyword Intelligence](docs/010-keyword-intelligence.md) | Volume + difficulty provider abstraction |
| [Apple Search Ads Analytics](docs/011-apple-search-ads-analytics.md) | ASA ingest, KPI dashboard, paid-organic join |
| [Growth Recommendations](docs/012-growth-recommendations.md) | Cross-domain growth advisor |

Full index: [docs/INDEX.md](docs/INDEX.md).

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions, and the PR process. By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Please do not open public issues for security reports.

## License

[Apache 2.0](LICENSE) © contributors.
