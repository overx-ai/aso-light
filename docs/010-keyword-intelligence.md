# 010 — Keyword Intelligence

A pluggable provider abstraction that feeds normalized **volume** + **difficulty**
scores per keyword/locale into a cache table. Stage 0 ships two free providers
backed by Apple Search Ads. Paid providers (MobileAction, AppTweak, AppFigures)
slot in behind the same ABC without touching consumers.

**Prerequisites**: [003 - Keyword Analysis](003-keyword-analysis.md) (the
keyword tracker + ranking surfaces this builds on),
[002 - ASC Integration](002-asc-integration.md) (parallel client patterns).

**Related**: [006 - Metadata Editor + Cross-Loc](006-metadata-editor.md) — the
Metadata grid is the first consumer; chips render the volume score inline.

---

## Why this exists

`Keyword.popularity` has been a column in the model since day one but
unfilled — the original assumption was that Apple Search Ads exposes a clean
"POST keywords → popularity" endpoint. **It doesn't (in v5).** Popularity only
surfaces on surfaces tied to ad groups *you own*: recommendations,
targeting-keyword bids, search-term reports. The internal `searchads.apple.com`
dashboard endpoint that some tools scrape is ToS-grey and broke in Oct 2025
when ~77% of US keywords pinned to the floor value `5`.

The honest framing: **Stage 0 is free but limited**. We surface scores for
keywords Apple already knows about for your account. For arbitrary-keyword
lookup (e.g. greenfield ASO research), Stage 1 paid providers are required —
their integration is unblocked, just not paid for yet.

---

## Architecture

```
app/services/keyword_intel/
  __init__.py
  base.py                  # KeywordIntelProvider ABC + KeywordIntel + upsert_intel
  asa_search_terms.py      # Path B: aggregates ASAMetricDaily → volume + difficulty
  asa_recommendations.py   # Path A: GET /v5/.../recommendations/keywords harvest
  # Future:
  # mobileaction.py
  # apptweak.py
  # appfigures.py
```

```
POST /apps/:id/keyword-intel/refresh?days=30
   ▼
for provider in (ASASearchTermsProvider, ASARecommendationsProvider, …):
    rows = await provider.fetch(app_id, session, days=…)
    upsert_intel(session, app_id, rows)
   ▼
KeywordIntelRefreshOut {written_total, by_source, skipped_sources}


GET /apps/:id/keyword-intel?keyword=foo&keyword=bar&locale=US
   ▼
keyword_intel_cache  (SELECT, newest first)
   ▼
KeywordIntelOut[]
```

### Provider contract

```python
@dataclass
class KeywordIntel:
    keyword: str
    locale: str          # alpha-2 country code (Apple's storefront key)
    source: str          # provider's stable name
    volume_score: int | None       # normalized 0–100
    difficulty_score: int | None   # normalized 0–100
    raw_score: int | None          # provider-native scale (debug)
    extra: dict[str, Any]          # provider-specific JSON

class KeywordIntelProvider(ABC):
    name: str
    @abstractmethod
    async def fetch(self, *, app_id, session, **kwargs) -> list[KeywordIntel]:
        ...

async def upsert_intel(session, app_id, rows) -> int: ...
```

Providers are **pure with respect to the DB session** — they call out to their
upstream and return rows. Persistence is the caller's responsibility (via
`upsert_intel`) so the ABC stays test-friendly.

---

## Path A — ASA recommendations harvest

`asa_recommendations.py`. Calls `GET
/v5/campaigns/{c}/adgroups/{a}/recommendations/keywords` for up to 5 ad groups
per app (preferring `ENABLED + ENABLED`). Apple returns an array of
suggested keywords, each with a `popularity` score on either the 1–5 dot
scale or the 5–100 integer scale; `_normalize_popularity` accepts both and
clamps to 0–100. `difficulty_score` is **not** populated by this surface —
left as `null`.

Auth-bubble behavior: `ASAAPIError` with status 401/403 is re-raised so the
refresh route reports it as a `skipped_sources[asa_recommendations]`. Other
4xx/5xx and `httpx.HTTPError` log + continue (per-ad-group transient errors:
closed group, not yet eligible for recs, etc.).

**Limits** (inherent — won't be fixed by code):

- Surfaces only Apple-suggested keywords, not arbitrary lookups.
- Oct 2025 floor-pegging means ~77% of US scores are `5`. The Metadata-grid
  chip badge hides scores below 30 to avoid false-signal display; the value
  is still in the tooltip.
- Apps with no ASA spend get an empty result set.

---

## Path B — Search-term-derived signals

`asa_search_terms.py`. Aggregates existing `ASASearchTerm` × `ASAMetricDaily`
(`dim_kind="SEARCH_TERM"`) rows over a configurable lookback window. **No
external calls** — pure DB read.

| Output | Formula |
|---|---|
| `volume_score` | `log10(1 + avg_daily_impressions) / log10(1 + ceiling) × 100` |
| `difficulty_score` | `0.6 × min(1, CPT/$5) + 0.4 × max(0, 1 − min(1, TTR/0.05)) × 100` |
| `raw_score` | `int(avg_daily_impressions)` |

The log scale keeps the long tail visible — without it, a handful of
high-traffic terms collapse the histogram.

`difficulty_score`'s rationale: high CPT + low TTR ≈ contested supply with
poor match quality. It's a heuristic, not a canonical "ASA difficulty"
metric (which Apple doesn't expose).

Skips terms with <5 total impressions in the window to keep the cache clean.

---

## DB schema

`keyword_intel_cache` — one row per `(app_id, keyword, locale, source)`.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `app_id` | int FK→apps.id ondelete CASCADE | indexed |
| `keyword` | string(255) | |
| `locale` | string(16) | alpha-2 country today; ICU later when paid providers ship |
| `source` | string(48) | `asa_search_terms` / `asa_recommendations` / future paid providers |
| `volume_score` | int? | 0–100 |
| `difficulty_score` | int? | 0–100 |
| `raw_score` | int? | provider native |
| `extra` | JSON? | provider-specific debug payload |
| `fetched_at` | datetime tz | server default `now()` |

Unique `(app_id, keyword, locale, source)` lets multiple sources coexist for
the same keyword — consumers can compare or pick. Secondary index
`(app_id, locale, keyword)` for the "show me intel for *this set* of keywords
in *this locale*" lookup the Metadata grid uses.

Migration: `03e831a0b230_add_keyword_intel_cache.py`.

---

## REST surface

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/apps/{app_id}/keyword-intel/refresh?days=30` | Runs every registered provider; returns `{written_total, by_source, skipped_sources}` |
| `GET` | `/api/v1/apps/{app_id}/keyword-intel?keyword=x&keyword=y&locale=US&source=…&limit=200` | Reads cache, newest first; multi-keyword via repeated `keyword=` |

A bad provider doesn't abort the refresh — it logs into `skipped_sources` and
the rest still run.

---

## MCP surface

| Tool | Mirrors | Notes |
|---|---|---|
| `keyword_intel_list(app_id, keyword?, locale?, source?, limit=200)` | `GET .../keyword-intel` | Cache read, newest first; `keyword` is a list |
| `keyword_intel_refresh_providers(app_id, provider?, days=30)` | `POST .../keyword-intel/refresh` | `provider` narrows the run to one source; an unknown name is a `ToolError` |

Both run the same `resolve_app` ownership chain as every other app-scoped tool
and share their bodies with the REST routes via
`app/services/keyword_intel/service.py`.

Do **not** confuse `keyword_intel_refresh_providers` with
`keywords_refresh_rankings` — the latter re-scrapes iTunes SERP ranks for
tracked keywords and writes nothing to this cache. Two mislabeled aliases
shipped before the real tools existed and were **both deleted**, not renamed —
each duplicated a `keywords_*` tool verbatim: `keyword_intel_refresh` →
`keywords_refresh_rankings`, `keyword_intel_list_for_app` →
`keywords_list_for_app`.

---

## Frontend integration

[Metadata grid](006-metadata-editor.md) is the first consumer.

- Bulk lookup: one `GET /keyword-intel?keyword=…&keyword=…` per grid load
  (deduped, lowercased keys), per-row chips look up in O(1) from a
  client-side `Map<country, Map<keyword, intel>>`.
- Locale resolver: `localeToCountry(r.locale)` extracts the last 2-letter
  uppercase chunk so `en-US → US`, `pt-BR → BR`, `zh-Hans-CN → CN`. Edge
  case: bare 2-letter language codes like `"en"` resolve to `"EN"` and may
  produce false-positive bucket lookups; rare in practice.
- "Fetch intel" button at the top of the grid invokes the refresh endpoint
  and invalidates the React Query cache for `["keyword-intel", appId]`.
- Volume score appears as a `📊 NN` `rightSection` on each chip when
  `volume_score >= 30`. Threshold guards against the Apple floor-pegging
  bug.
- Tooltip suffix on hover: `· vol N / diff N (source)` whenever intel is
  cached, regardless of the visible threshold.

Frontend types + hook: `frontend/src/lib/hooks.ts` exports `useKeywordIntel`,
`useRefreshKeywordIntel`, `bestIntelByKeyword`, `KeywordIntelOut`,
`KeywordIntelRefreshOut`.

---

## Stage 1 — paid providers (planned, not shipped)

The provider abstraction was designed for this. Adding any of MobileAction /
AppTweak / AppFigures is two pieces:

1. New `app/services/keyword_intel/<vendor>.py` subclassing
   `KeywordIntelProvider`. Returns `KeywordIntel` rows with the same
   normalized 0–100 score range (each vendor's native scale gets mapped
   inside the provider; document the mapping in the file's docstring).
2. Append the class to `PROVIDER_FACTORIES` in
   `app/services/keyword_intel/service.py` (REST + MCP both read it).

Consumers (Metadata grid, future Clash + Keywords pages) **don't change**.
The cache key includes `source` so paid + free coexist; "best intel" picking
is `bestIntelByKeyword(rows)` — currently picks the highest `volume_score`
with freshness as the tiebreaker.

Ballpark pricing per vendor (2026):

| Vendor | Cost | What it unlocks |
|---|---|---|
| MobileAction | $69–$499/mo | Volume + difficulty for arbitrary keywords + competitor ranks |
| AppTweak | $79–$649/mo | Same, broader locales |
| AppFigures | $49–$499/mo | Same + bundled download/revenue estimates as upsell |
| Sensor Tower | $1.5–4k/mo | Top-tier accuracy |
| data.ai | $3–8k/mo | Gold standard |

---

## Key files

| Layer | File |
|---|---|
| Model | `backend/app/models/keyword_intel.py` |
| Service ABC | `backend/app/services/keyword_intel/base.py` |
| Path A provider | `backend/app/services/keyword_intel/asa_recommendations.py` |
| Path B provider | `backend/app/services/keyword_intel/asa_search_terms.py` |
| Schema | `backend/app/schemas/keyword_intel.py` |
| Shared read/refresh | `backend/app/services/keyword_intel/service.py` (`PROVIDER_FACTORIES`, `run_providers`, `list_intel`) |
| REST route | `backend/app/api/v1/keyword_intel.py` |
| MCP tools | `backend/app/mcp/tools/keywords.py` (`keyword_intel_list`, `keyword_intel_refresh_providers`) |
| Migration | `backend/alembic/versions/03e831a0b230_add_keyword_intel_cache.py` |
| Frontend hook + types | `frontend/src/lib/hooks.ts` (`useKeywordIntel`, `useRefreshKeywordIntel`, `bestIntelByKeyword`) |
| Frontend consumer | `frontend/src/components/metadata/MetadataGrid.tsx` |

---

## Limits + future work

- **Server-side cooldown** on `POST .../keyword-intel/refresh` — frontend
  button gating prevents user double-click but a CLI/MCP caller can hammer
  ASA. A 60s "minimum-interval" check against `max(fetched_at)` would fix
  this.
- **Wire intel into App Clash + Keywords pages** — same chip pattern as
  Metadata grid, just two more consumers.
- **Stage 1 paid provider** — to validate the abstraction holds and unlock
  arbitrary-keyword volume/difficulty.
