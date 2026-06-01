# 012 - Growth Recommendations Advisor

**Prerequisites**: [003 - Keyword Analysis](003-keyword-analysis.md), [006 - Metadata Editor + Cross-Loc](006-metadata-editor.md), [009 - Reviews Theme Classifier](009-reviews-theme-classifier.md), [010 - Keyword Intelligence](010-keyword-intelligence.md), [011 - Apple Search Ads Analytics](011-apple-search-ads-analytics.md)
**Related**: [001 - Pricing System](001-pricing-system.md)

## Overview

The Growth Advisor surfaces a prioritized list of actionable recommendations by cross-referencing data from every domain (metadata, keywords, paid search, reviews, pricing). Each card has a category badge, priority/confidence/effort labels, a human-readable explanation, evidence keys, and a deep-link CTA — no AI generation, pure signal-based rules.

## Architecture

**Service**: `backend/app/services/growth/recommendations.py`
**API**: `backend/app/api/v1/growth.py` — `GET /apps/{app_id}/growth/recommendations`
**Frontend**: `frontend/src/pages/GrowthPage.tsx`

```
GrowthRecommendation (frozen dataclass)
  id           str        # stable, dot-namespaced (e.g. "asa.add_negative_keywords")
  category     Literal["setup","metadata","keywords","paid_search","reviews","pricing"]
  priority     Literal["high","medium","low"]
  confidence   Literal["high","medium","low"]
  effort       Literal["high","medium","low"]
  title        str
  detail       str
  evidence     dict[str, Any]   # domain facts surfacing why this fired
  cta_label    str
  cta_path     str              # relative frontend path for the action button
```

## Recommendation Rules

`generate_growth_recommendations(session, app_id)` queries the DB and returns up to one recommendation per signal, sorted by `(priority ASC, effort ASC)`:

| id | Signal | Evidence keys |
|----|--------|---------------|
| `metadata.sync` | No `AppMetadataState` or `AppMetadataLocalization` rows for app | `metadata_synced: False` |
| `keywords.expand_tracking` | Fewer than 5 tracked keywords | `tracked_keywords, recommended_minimum` |
| `metadata.keyword_coverage` | Tracked keywords absent from all cached title/subtitle/keywords fields | `missing_keywords[:5], missing_count` |
| `asa.track_paid_winners` | ASA search terms with `taps >= 20` (30-day window) not in organic tracker | `candidate_count, top_term, top_taps, top_installs` |
| `asa.add_negative_keywords` | ASA search terms with `spend >= $10` and `conversion_rate <= 0.5%` (30-day) | `candidate_count, top_term, top_spend, top_conversion_rate` |
| `reviews.triage_severe` | `ReviewThemeCache` rows with `severity >= 4` and `theme in {bug,ux,pricing,support}` within 30 days | `severe_review_count` |

> The 30-day window on `reviews.triage_severe` ensures the card fades once the team has worked through a backlog; it is not driven by the full history.

## Adding a New Recommendation

1. Add a private `async def _my_signal(session, app_id) -> <evidence>` query in `recommendations.py`.
2. Call it inside `generate_growth_recommendations` and `.append()` a `GrowthRecommendation`.
3. Pick a stable `id` (e.g. `pricing.localize_high_gdp`) and a `cta_path` that matches an existing route.
4. Update the category icon map in `frontend/src/pages/GrowthPage.tsx` if adding a new `category`.

No schema migration needed — `GrowthRecommendation` is not persisted.

## Frontend

**File**: `frontend/src/pages/GrowthPage.tsx`

- Route: `/apps/:id/growth` (the per-app default route — clicking an app navigates here).
- Fetches `GET /apps/:id/growth/recommendations` via `useGrowthRecommendations(appId)`.
- Renders a `SimpleGrid` of `RecommendationCard` components, each showing:
  - Category badge + icon (coloured by category)
  - Priority badge (red/yellow/gray)
  - Confidence + effort chips
  - `Progress` bar encoding priority as a score (high=100, medium=62, low=34)
  - Evidence table (key → formatted value)
  - CTA `Button` that navigates to `cta_path`
- "Refresh" button invalidates the query key and re-fetches.

`CATEGORY_META` maps each `GrowthCategory` to `{label, color, icon}`.

## Query Hook

`useGrowthRecommendations(appId)` in `frontend/src/lib/hooks.ts`:
```ts
queryKey: queryKeys.growth.recommendations(appId)
queryFn:  GET /apps/${appId}/growth/recommendations
staleTime: 5 minutes
```

## Limitations & Roadmap

- **No persistence** — recommendations are regenerated on every request; there is no "dismissed" or "completed" state.
- **Pricing recommendations** — the `pricing` category is wired in the frontend icon map but no rule fires yet. Candidate signals: un-localized prices (USD in non-US territories), large price gaps vs PPP baseline.
- **Scheduling** — all recommendations are computed synchronously on GET; adding a background job to pre-cache recommendations hourly would improve page latency for large accounts.
