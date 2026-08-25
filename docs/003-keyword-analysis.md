# 003 - Keyword Analysis

**Prerequisites**: [000 - Architecture](000-architecture.md)

## Overview

The keyword analysis feature lets users track app rankings for search terms, discover new keywords via autocomplete, understand cross-localization (which locales are indexed in which territories), and monitor competitor keyword strategies.

## Data Sources

| Feature | Source | API | Notes |
|---------|--------|-----|-------|
| Keyword suggestions | iTunes Search Hints | `search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints` | Unofficial but stable |
| Keyword ranking | iTunes Search API | `itunes.apple.com/search` | Position in results = rank |
| Cross-localization | Static data | None | Community knowledge, hardcoded |
| Keyword popularity (0-100) | Apple Search Ads API | OAuth2 required | Needs separate account — **not yet implemented** |

## iTunes Search Hints (Autocomplete)

**File**: `backend/app/services/keywords/suggestions.py` — `ITunesSuggestionsService`

```
GET https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints
    ?clientApplication=Software&media=software&term=<term>
X-Apple-Store-Front: <storefront_id>-1,29
```

Returns suggested keyword completions. Used for the Search tab's autocomplete input.

**The `X-Apple-Store-Front` header is mandatory.** Without it Apple answers HTTP 200
with an empty `<array/>` — no error, nothing logged — so the endpoint returns `[]`
for every term. The `l=` query param does *not* select a storefront and is no
longer sent; the header alone picks both the store and the language of the hints.

Storefront ids live in `backend/app/data/storefronts.py`, keyed by ISO alpha-2
country code (`us → 143441`, `de → 143443`). These are the **classic iTunes**
storefront numbers, unrelated to `Territory.apple_territory_id` (an ASC id).
`get_suggestions(term, country="us")` matches `ITunesSearchService.search_apps`;
the old `locale=` kwarg is still accepted for one release (`en_us → us`). An empty
result logs a warning with term + country, so a silent `[]` can never hide again.

## iTunes Search API (Ranking)

**File**: `backend/app/services/keywords/itunes_search.py` — `ITunesSearchService`

```
GET https://itunes.apple.com/search
    ?term=<keyword>&country=<code>&media=software&limit=200
```

Returns up to 200 app results in ranked order. The rank of the user's app = its position in results (1-indexed). If the app doesn't appear in top 200, rank is `null`.

## Keyword Ranking Tracker

**File**: `backend/app/services/keywords/tracker.py` — `KeywordRankingTracker`

Given an app and its tracked keywords:
1. For each `KeywordTracking` record, call `ITunesSearchService.search_apps()`
2. Find the app's position by matching `asc_app_id`
3. Store result as `KeywordRanking` record with `recorded_at` timestamp

Rank change = `previous_rank - latest_rank` (positive = improved, negative = dropped).

## Cross-Localization

**File**: `backend/app/services/keywords/cross_localization.py`

Static data — 114 entries mapping (territory_code, locale) pairs.

**Key concept**: In the US App Store, Apple indexes keywords from multiple locales (en-US, es-MX, fr-FR, pt-BR, zh-Hans, ko, vi, ar, ru). By filling metadata in secondary locales, developers effectively double or triple their keyword slots.

```
US App Store indexed locales:
  en-US, es-MX, ar, fr-FR, ko, pt-BR, ru, vi, zh-Hans, zh-Hant
```

The cross-localization matrix UI shows a grid of territory × locale with green cells for indexed combinations.

## Data Model

```
keyword (text, locale, popularity, popularity_updated_at)
    ↑ unique on (text, locale)

keyword_tracking (app_id FK, keyword_id FK)
    ↑ unique on (app_id, keyword_id) — tracks which keywords a user monitors for an app

keyword_ranking (tracking_id FK, territory_id FK, rank, recorded_at)
    ↑ historical rank records (one per tracking check)

keyword_locale_index (locale, territory_code, is_indexed)
    ↑ the cross-localization map

competitor_app (app_id FK, asc_app_id, name, bundle_id)
    ↑ competitor apps tracked relative to user's app
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/keywords/suggestions?term=...` | Autocomplete suggestions |
| `GET` | `/api/v1/keywords/cross-localization` | Full cross-locale matrix (public) |
| `GET` | `/api/v1/apps/{id}/keywords` | List tracked keywords |
| `POST` | `/api/v1/apps/{id}/keywords` | Add keyword to track |
| `DELETE` | `/api/v1/apps/{id}/keywords/{tracking_id}` | Stop tracking |
| `GET` | `/api/v1/apps/{id}/keywords/{tracking_id}/rankings` | Rank history |
| `POST` | `/api/v1/apps/{id}/keywords/refresh` | Refresh all rankings |
| `GET` | `/api/v1/apps/{id}/competitors` | List competitor apps |
| `POST` | `/api/v1/apps/{id}/competitors` | Add competitor |
| `DELETE` | `/api/v1/apps/{id}/competitors/{id}` | Remove competitor |
| `POST` | `/api/v1/apps/{id}/competitors/{id}/keywords` | Competitor keyword analysis |

## Frontend Components

**File**: `frontend/src/pages/KeywordsPage.tsx` — 4 tabs (Tracked, Search, Cross-Localization, Competitors)
**File**: `frontend/src/components/keywords/RankHistoryChart.tsx` — Mantine Charts line chart, Y-axis inverted (rank 1 at top)
**File**: `frontend/src/components/keywords/CrossLocalizationMatrix.tsx` — Territory × locale grid

## Future: Apple Search Ads Integration

Apple Search Ads API provides keyword popularity scores (0-100) — the same data visible in App Store Connect's "Search Ads" keyword planner. This requires:
1. A separate Apple Search Ads account (free to create)
2. OAuth2 credentials (different from ASC API keys)
3. `GET /v5/keywords/targeting/search?searchTerm=<term>` endpoint

Tracked in task T-006.
