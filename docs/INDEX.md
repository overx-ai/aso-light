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
| [006 - Metadata Editor + Cross-Loc](006-metadata-editor.md) | Per-locale app metadata CRUD, bulk fan-out, Claude AI translation, color-coded keyword coverage, cross-localization grid |
| [006 - Product Swap iOS Integration](006-product-swap-ios-integration.md) | Clone+archive subscription/IAP swap flow + the iOS-side checklist |
| [007 - MCP Integration](007-mcp-integration.md) | MCP server mounted at `/mcp`, Personal Access Token auth, tool reference, client config |
| [009 - Reviews Theme Classifier + Reply Queue](009-reviews-theme-classifier.md) | LLM-tagged review themes + severity, priority-sorted reply queue, theme filter chips |
| [010 - Keyword Intelligence](010-keyword-intelligence.md) | Provider-abstracted volume + difficulty cache; free ASA-derived providers |
| [011 - Apple Search Ads Analytics](011-apple-search-ads-analytics.md) | ASA ingest pipeline, KPI dashboard, REST + MCP tools, paid-organic join |
| [012 - Growth Recommendations Advisor](012-growth-recommendations.md) | Cross-domain recommendation engine powering the Growth page |
| [013 - Custom Product Pages + Visual Compare](013-custom-product-pages-and-visual-compare.md) | CPP CRUD + screenshot upload, Pillow before/after compositor, ASA→CPP ad-group wiring (`asa.assign_cpp` / `asa.unassign_cpp` / `asa.list_cpp_ads`) |
| [014 - Reviews Module Security Findings](014-reviews-module-security-findings.md) | `/code` review-pass findings for Review Responses (cross-app IDOR, uncapped AI drafts, cap-signal + cache-namespace bugs) — report only, fixes paused pending the C1 ASC review→app linkage decision |

## Specs

| Spec | Topic | Status |
|------|-------|--------|
| [004 - Cache Apple Price Points](specs/004-cache-apple-price-points.md) | DB caching for ASC price data | approved |
| [005 - GDP-Bracket Pricing](specs/005-gdp-bracket-pricing.md) | Absolute-price tiers driven by World Bank GDP/capita PPP | done |
| [006 - Subscription Management](specs/006-subscription-management.md) | Group / subscription / intro-offer write paths | done |
| [007 - Metadata Editor + Cross-Loc](specs/007-metadata-editor-and-cross-loc.md) | Phase 5 — metadata editor, AI translation, cross-loc grid | done |

## Documentation Tree

```
000-architecture.md (root)
├── 001-pricing-system.md
│   └── → 002-asc-integration.md (ASC API layer)
│       ├── → 004-localization-management.md
│       ├── → 005-subscription-management.md
│       └── → 006-metadata-editor.md (app-level metadata; sibling of 004)
└── 003-keyword-analysis.md
    └── → 006-metadata-editor.md (keyword coverage classifier)
011-apple-search-ads-analytics.md
    └── → 013-custom-product-pages-and-visual-compare.md (CPP CRUD + visual compare + ASA→CPP wiring)
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
| Metadata router | `backend/app/api/v1/metadata.py` |
| Metadata services | `backend/app/services/metadata/{client,snapshot,bulk,validation,coloring,translate}.py` |
| Shared API deps | `backend/app/api/v1/_deps.py` (ownership check + ASC client factory) |
| Metadata page | `frontend/src/pages/MetadataPage.tsx` |
| Cross-Localization page | `frontend/src/pages/CrossLocalizationPage.tsx` |
| Metadata components | `frontend/src/components/metadata/*` |
| CPP service | `backend/app/services/asc/cpp.py` (CRUD + screenshot upload + default/CPP screenshot fetch) |
| Visual compositor | `backend/app/services/visual/compare.py` (Pillow before/after montage) |
| ASA→CPP ad wiring | `backend/app/services/asa/cpp_ads.py` + `backend/app/mcp/tools/asa.py` (`asa.assign_cpp` / `asa.unassign_cpp` / `asa.list_cpp_ads`) |

---
*Last updated: 2026-06-16*
