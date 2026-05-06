---
id: 009
title: "Keyword visibility tracker (Search Ads-style competitor intel)"
status: draft
created: 2026-05-06
tasks: []
---

# 009 - Keyword Visibility Tracker

## Problem

aso.dev's ASA features (#33–36) — App Advertising, Keyword Advertising, Share of Voice, Auction Analysis — give competitor visibility intel for keyword × country pairs. Their data source is undocumented but appears to scrape Apple's storefront pages where sponsored slots are tagged.

**Honest MVP scope**: Apple's public iTunes Search API returns the top organic results for a query but **does not expose paid placements**. We build the same shape of tool — watched keywords × countries, time-series snapshots, share-of-voice — but powered by organic SERP data only. The framing is "Keyword Visibility" rather than "Apple Search Ads"; if the user later supplies real ASA Reporting API credentials we can layer paid metrics on the same models.

## Scope

In (MVP):
- **SERP polling**: given (keyword, country), call `ITunesSearchService.search_apps` and record top 20 organic positions with timestamp.
- DB models: `KeywordVisibilityWatch`, `KeywordVisibilitySnapshot`, `KeywordVisibilityResult`.
- Snapshot trigger: per-row "Poll now" button (no scheduler in MVP).
- Endpoints + UI for:
  - **Keyword view** — for any watched (keyword, country), show current top 20 + 30-day timeline of position changes.
  - **App view** — for any track-id we've seen, list every watched keyword it has appeared on + best/worst rank.
  - **SOV** — bar chart per watched keyword: % of polls in last N days where each app held a top-3 slot.

Out (deferred):
- Real paid-ad detection (requires storefront scraping; out of scope).
- Apple Search Ads Reporting API integration (requires per-account OAuth; not in MVP).
- Creative / CPP detection.
- Bid / spend estimation.
- Scheduled background polling (manual trigger only — APScheduler hook can be added later).

## Architecture

**Backend models** — `backend/app/models/visibility.py`
- `KeywordVisibilityWatch(id, app_id, text, country, added_at, last_polled_at)` — per-app watch-list.
- `KeywordVisibilitySnapshot(id, watch_id, polled_at, results_count)` — one row per poll.
- `KeywordVisibilityResult(id, snapshot_id, position, track_id, name, bundle_id, icon_url)` — one row per result in a snapshot (capped at top 20).

Cascading delete on watch → snapshot → result.

**Backend service** — `backend/app/services/visibility/poller.py`
- Reuses `ITunesSearchService.search_apps(term, country, limit=20)` (already returns organic top results with `app_id, name, bundle_id, icon_url`).
- `poll_watch(watch, session, *, search_service)`:
  1. Call `search_apps` for `(watch.text, watch.country, 20)`.
  2. Create `KeywordVisibilitySnapshot` + N `KeywordVisibilityResult` rows.
  3. Bump `watch.last_polled_at`.
  4. Caller commits.

**API** — `backend/app/api/v1/visibility.py`, mounted under `/apps`:
- `GET /apps/{app_id}/visibility/watches` → list watches + last poll + last snapshot summary.
- `POST /apps/{app_id}/visibility/watches` body `{text, country}` → add watch.
- `DELETE /apps/{app_id}/visibility/watches/{id}` → remove.
- `POST /apps/{app_id}/visibility/watches/{id}/poll` → poll now, returns the snapshot.
- `GET /apps/{app_id}/visibility/watches/{id}/snapshots?since=` → time-series.
- `GET /apps/{app_id}/visibility/sov?days=30` → per-watch top-3 share-of-voice over the last N days.

**Frontend** — `frontend/src/pages/VisibilityPage.tsx` + `frontend/src/components/visibility/`
- Sub-nav entry "Visibility".
- Watches table: keyword + country + last poll + "Poll now" + delete + click → drilldown drawer.
- Drilldown drawer: latest top 20, plus position-over-time line chart for the operator's own app (optional — requires recording our own track id; pulled from `App.asc_app_id`).
- SOV tab: BarChart per watched keyword showing top-3 share by track id.

## Edge cases

- **iTunes returns no ads** — typical for low-traffic queries; treat as "no slot occupied". Still bump `last_polled_at`.
- **Country normalisation** — store lowercase ISO-2 (e.g. "us"); validate against `app/data/territories.py`.
- **Rate limits** — Apple throttles store search ~100 req/hr per IP. Spread polls; cap per-poll-call burst to 1 req.
- **Schema drift** — Apple's storefront JSON shape changes occasionally; wrap parsing in try/except, log raw shape on failure.
- **Privacy** — we do not persist any user-identifying data, only public iTunes payload fields.

## Verification

1. Add a watched keyword (e.g. `meditation`, `us`).
2. Click **Poll now** — backend logs "found N sponsored slots", DB row added.
3. Re-poll a few times; chart shows multiple snapshots.
4. SOV tab shows per-app share for the keyword over the polling window.

## Critical files (new + edit)

- New `backend/app/models/visibility.py`
- New Alembic migration `backend/alembic/versions/004_visibility_tables.py`
- New `backend/app/services/visibility/__init__.py`, `poller.py`
- New `backend/app/api/v1/visibility.py` + register in `backend/app/api/v1/__init__.py`
- New `backend/app/schemas/visibility.py`
- New `frontend/src/pages/VisibilityPage.tsx`
- New `frontend/src/components/visibility/*.tsx`
- Edit `frontend/src/lib/hooks.ts` — visibility hooks
- Edit `frontend/src/types/index.ts` — visibility types
- Edit `frontend/src/App.tsx` — route
- Edit `frontend/src/components/AppNavItem.tsx` — sub-nav link
