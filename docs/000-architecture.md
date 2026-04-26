# 000 - Architecture

## Overview

ASO-Light is a web-based App Store Optimization SaaS tool — a focused alternative to aso.dev. It enables app developers to manage App Store prices across all 175+ territories using economic multipliers (PPP, Big Mac, Netflix, Spotify) and track keyword performance.

## System Components

| Component | Purpose | Tech Stack |
|-----------|---------|------------|
| Backend API | REST API, business logic, ASC integration | FastAPI (Python 3.12+) |
| Database | Persistent storage | SQLite (dev) / PostgreSQL (prod) |
| Frontend SPA | User interface | React 19 + Mantine v8 + TypeScript |
| ASC API Client | App Store Connect integration | httpx + PyJWT (ES256) |
| Rate Cache API | Live exchange rates (166 currencies) | External: `api.overx.ai` |

## Data Flow

```
Browser (React SPA)
       │
       │ REST /api/v1/*
       ▼
FastAPI Backend
       │              │                   │
       ▼              ▼                   ▼
 PostgreSQL     Apple ASC API       External APIs
 (SQLite dev)   api.appstoreconnect  api.overx.ai (FX rates)
                .apple.com           World Bank PPP
                                     Economist CSV
                                     iTunes Search
                                     iTunes Hints
```

## Authentication Flow

```
Register/Login → JWT (HS256, 30min access + 7day refresh)
                 │
                 ▼ Authorization: Bearer <token>
              FastAPI get_current_user() → user_id
```

## ASC API Authentication

```
.p8 key (Fernet encrypted at rest)
  → decrypt in memory
  → PyJWT ES256 sign (20min lifetime)
  → Authorization: Bearer <asc_token>
  → api.appstoreconnect.apple.com
```

## Service Architecture

```
backend/app/
├── api/v1/           # Route handlers (thin layer)
│   ├── auth.py
│   ├── credentials.py
│   ├── apps.py
│   ├── pricing.py
│   ├── territories.py
│   ├── keywords.py
│   ├── presets.py
│   ├── export.py
│   └── indices.py
├── services/
│   ├── asc/          # App Store Connect API client
│   │   ├── client.py           ← ASCClient base (JWT, pagination, rate limit, throttle)
│   │   ├── apps.py
│   │   ├── pricing.py          ← Subs + IAP prices, localizations, screenshots
│   │   ├── price_point_cache.py ← Filesystem cache for Apple price tiers
│   │   └── errors.py
│   ├── pricing/      # Price calculation engine
│   │   ├── calculator.py       ← PriceCalculator ABC
│   │   ├── engine.py           ← PriceEngine (compose: calc → VAT → charming)
│   │   ├── exchange_rate.py    ← ExchangeRateCalculator (live FX rates)
│   │   ├── currency_rounding.py ← Smart currency-aware rounding (±10% flex)
│   │   ├── ppp.py / bigmac.py / netflix.py / spotify.py / fixed_payout.py
│   │   ├── charming.py
│   │   └── vat.py
│   ├── rates/        # Exchange rate API client
│   │   └── client.py      ← RateCacheClient (api.overx.ai)
│   ├── indices/      # Economic data fetchers
│   │   ├── base.py       ← IndexFetcher ABC
│   │   ├── ppp.py        ← World Bank API
│   │   ├── bigmac.py     ← Economist GitHub CSV
│   │   ├── netflix.py    ← Seed data (~70 countries)
│   │   ├── spotify.py    ← Seed data (~70 countries)
│   │   └── refresh.py    ← IndexRefreshService orchestrator
│   ├── keywords/     # Keyword analysis
│   │   ├── suggestions.py    ← iTunes Search Hints
│   │   ├── itunes_search.py  ← iTunes Search API (ranking)
│   │   ├── tracker.py        ← KeywordRankingTracker
│   │   └── cross_localization.py ← Static locale→territory map
│   └── export/       # Excel/CSV export+import
│       ├── excel.py
│       └── csv.py
├── models/           # SQLAlchemy 2.0 ORM models
├── schemas/          # Pydantic request/response schemas
├── core/
│   ├── config.py     # pydantic-settings (.env)
│   └── security.py   # JWT, Fernet, bcrypt
└── data/
    ├── territories.py  # 202 territory seed records
    └── seed.py         # Idempotent seeding
```

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Private key storage | Fernet symmetric encryption at rest | .p8 keys must never be plaintext in DB |
| ASC auth | ES256 JWT via PyJWT | Apple requires ES256, not HS256 |
| App auth | HS256 JWT via python-jose | Standard SaaS auth |
| Password hashing | bcrypt (direct, not passlib) | passlib/bcrypt version incompatibility |
| Price math | `Decimal` throughout | Avoid floating-point rounding errors |
| Calculators | PriceCalculator ABC + ProportionalCalculator base | PPP/BigMac/Netflix/Spotify/ExchangeRate share identical formula |
| Currency rounding | Per-currency profiles with ±10% flex | JPY/KRW/INR/VND need different rounding than USD/EUR |
| Exchange rates | Live from api.overx.ai (rate-cache-api) | 166 currencies, cached hourly, no auth needed |
| Price point cache | Filesystem JSON per territory | Avoids 20min bulk fetch of ~140k price points |
| ASC rate limiting | 150ms throttle + exponential backoff | Prevents 429s on bulk operations |
| Frontend tables | mantine-datatable | Better virtualization for 175-row price grid |
| DB (dev) | SQLite + aiosqlite | Zero-config local development |
| DB (prod) | PostgreSQL + asyncpg | Multi-user SaaS needs real DB |

## Multi-Tenancy Model

Each user owns their credentials → credentials own apps → apps own all pricing/keyword data. All API endpoints verify the ownership chain: `app.credential_id → credential.user_id == current_user_id`.

## External Data Sources

| Data | Source | Update Frequency |
|------|--------|-----------------|
| PPP indices | World Bank API | Annual |
| Big Mac Index | Economist GitHub CSV | Semi-annual |
| Netflix prices | Seed data (~70 countries) | Manual/quarterly |
| Spotify prices | Seed data (~70 countries) | Manual/quarterly |
| VAT rates | Seed data (territory model) | Annual |
| Keyword popularity | Apple Search Ads API | Real-time |
| Keyword suggestions | iTunes Search Hints (unofficial) | Real-time |
| App rankings | iTunes Search API | Real-time |
| Cross-localization | Static Python dict | Rare |
| Exchange rates | rate-cache-api (`api.overx.ai`) | Hourly (cached) |
