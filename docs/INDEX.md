# Documentation Index

## Quick Links

- [Architecture](000-architecture.md) — System overview, components, design decisions
- [Changelog](000-changelog.md) — Version history
- [Tasks](000-tasks.md) — Active, completed, and backlog tasks

## Feature Documentation

| Doc | Topic |
|-----|-------|
| [001 - Pricing System](001-pricing-system.md) | Price calculators, exchange rates, currency rounding, safety limits, price point cache, manual pins, IAP pricing, presets, export/import |
| [002 - ASC Integration](002-asc-integration.md) | App Store Connect API (v1+v2), JWT auth, rate limiter, screenshot upload, .p8 key handling |
| [003 - Keyword Analysis](003-keyword-analysis.md) | Ranking tracker, cross-localization, suggestions |
| [004 - Localization Management](004-localization-management.md) | Subscription/IAP display names & descriptions, bulk sync, JSON import |
| [005 - Subscription Management](005-subscription-management.md) | Create / update groups, subscriptions, group localizations, introductory offers (CRUD via UI; submit-for-review manual) |

## Specs

| Spec | Topic | Status |
|------|-------|--------|
| [004 - Cache Apple Price Points](specs/004-cache-apple-price-points.md) | DB caching for ASC price data | approved |
| [005 - GDP-Bracket Pricing](specs/005-gdp-bracket-pricing.md) | Absolute-price tiers driven by World Bank GDP/capita PPP | done |
| [006 - Subscription Management](specs/006-subscription-management.md) | Group / subscription / intro-offer write paths | done |

## Documentation Tree

```
000-architecture.md (root)
├── 001-pricing-system.md
│   └── → 002-asc-integration.md (ASC API layer)
│       ├── → 004-localization-management.md
│       └── → 005-subscription-management.md
└── 003-keyword-analysis.md
010-audit.md (planned — summarizes 001-009)
```

## Key Files Reference

| Area | Key File |
|------|----------|
| FastAPI app entry | `backend/app/main.py` |
| ASC client | `backend/app/services/asc/client.py` |
| ASC pricing service | `backend/app/services/asc/pricing.py` |
| Price point cache | `backend/app/services/asc/price_point_cache.py` |
| Price engine | `backend/app/services/pricing/engine.py` |
| Currency rounding | `backend/app/services/pricing/currency_rounding.py` |
| Rate cache client | `backend/app/services/rates/client.py` |
| Territory data + alpha mapping | `backend/app/data/territories.py` |
| Pricing API endpoints | `backend/app/api/v1/pricing.py` |
| Price grid UI | `frontend/src/components/pricing/PriceGrid.tsx` |
| Localization editor | `frontend/src/components/pricing/LocalizationEditor.tsx` |
| Subscription / group / intro-offer modals | `frontend/src/components/pricing/{SubscriptionGroupFormModal,GroupLocalizationsModal,SubscriptionFormModal,IntroOffersModal}.tsx` |
| Screenshot upload | `frontend/src/components/pricing/ReviewScreenshotUpload.tsx` |
| Keyword page | `frontend/src/pages/KeywordsPage.tsx` |
| TanStack hooks | `frontend/src/lib/hooks.ts` |

---
*Last updated: 2026-05-01*
