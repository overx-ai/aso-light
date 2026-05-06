---
id: 009
title: "Apple Search Ads competitor intel — sponsored slot tracking"
status: draft
created: 2026-05-06
tasks: []
---

# 009 - Apple Search Ads Competitor Intel

## Problem

aso.dev exposes 4 ASA features (#33–36 in the parity research):
- **App Advertising** — every ad campaign of a given app: keywords + creatives + CPPs.
- **Keyword Advertising** — which apps run ads for a query, plus their creatives.
- **Share of Voice** — over-time ad SOV for our keywords vs. competitors.
- **Auction Analysis** — live snapshot of who occupies each ad slot for a keyword.

All four are read-only **competitor intel** — they do not manage our own campaigns. The data is gathered by polling iTunes Search results across countries; ads appear with a `kind: "iosSoftwareAd"` marker in iTunes JSON (or a `Sponsored` flag in the public web search). No ASA OAuth is required for this.

## Scope

In (MVP):
- **Sponsored-slot polling**: given (keyword, country), call iTunes search and record any sponsored result with timestamp.
- DB models: `AsaSponsoredSnapshot`, `AsaWatchedKeyword`.
- Snapshot job: cron-style refresh of the per-app watch-list (manual trigger first, scheduled later).
- Endpoints + UI for:
  - **Keyword Ads** — for any (keyword, country), show current sponsored apps + 30-day timeline.
  - **App Ads** — for any (app_id, country), show every keyword we've seen them advertise on + creative if known.
  - **SOV** — bar chart per watched keyword: % of polls in last N days where each app held the slot.

Out (deferred):
- Real-time auction snapshot (Apple's auction state API is private).
- Creative metadata (we only see the icon + name from iTunes search; full creative requires the ASC ASA API which is per-account).
- Per-territory CPP detection.
- Bid amount / spend estimation.

## Architecture

**Backend models** — `backend/app/models/asa.py`
- `AsaWatchedKeyword(id, app_id, text, country, added_at, last_polled_at)` — the watch-list per app.
- `AsaSponsoredSnapshot(id, watched_id, polled_at, sponsored_app_track_id, sponsored_app_name, sponsored_app_bundle_id, sponsored_icon_url, slot_index)` — one row per (keyword, country, time, slot).

**Backend service** — `backend/app/services/asa/poller.py`
- Reuses `ITunesSearchService.search_apps()` BUT the public iTunes Search API does **not** return ads. We need the iTunes "store" search endpoint (`https://search.itunes.apple.com/WebObjects/MZSearch.woa/wa/search?clientApplication=Software&media=software&term=...&country=...`) which returns a richer payload that does include the `kind: "iosSoftwareAd"` storefront slot. New service: `ITunesStoreSearchService` next to the existing one.
- `poll_keyword(watched, session)`:
  1. Call store search → parse storefront items, identify `kind == "iosSoftwareAd"` (slot 0) and any `flags == "Sponsored"`.
  2. Insert one snapshot row per ad detected.
  3. Bump `watched.last_polled_at`.

**API** — `backend/app/api/v1/asa.py`, mounted under `/apps`:
- `GET /apps/{app_id}/asa/keywords` → list watched keywords + last poll.
- `POST /apps/{app_id}/asa/keywords` body `{text, country}` → add watch.
- `DELETE /apps/{app_id}/asa/keywords/{id}` → unwatch.
- `POST /apps/{app_id}/asa/keywords/{id}/poll` → run a poll now, returns latest snapshot.
- `GET /apps/{app_id}/asa/keywords/{id}/snapshots?since=&until=` → time-series for charts.
- `GET /apps/{app_id}/asa/sov?days=30` → SOV summary per watched keyword.

**Frontend** — `frontend/src/pages/AsaAnalyticsPage.tsx` + `frontend/src/components/asa/`
- Sub-nav entry "Search Ads".
- Three tabs: **Keyword Ads**, **App Ads** (table with row per app track id we've seen advertising), **SOV** (Mantine `BarChart`).
- Add-keyword inline form: text + country.
- "Poll now" button per row.

**Scheduling** (deferred to a follow-up; out of MVP):
- Add APScheduler hook in `backend/app/main.py` that polls every watched keyword once per hour. For MVP, manual button only.

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

- New `backend/app/models/asa.py`
- New Alembic migration `backend/alembic/versions/004_asa_tables.py`
- New `backend/app/services/asa/poller.py`
- New `backend/app/services/keywords/itunes_store_search.py` (or extend existing)
- New `backend/app/api/v1/asa.py` + register in `backend/app/api/v1/__init__.py`
- New `backend/app/schemas/asa.py`
- New `frontend/src/pages/AsaAnalyticsPage.tsx`
- New `frontend/src/components/asa/*.tsx`
- Edit `frontend/src/lib/hooks.ts` — add ASA hooks
- Edit `frontend/src/types/index.ts` — add ASA types
- Edit `frontend/src/App.tsx` — route
- Edit `frontend/src/components/AppNavItem.tsx` — sub-nav link
