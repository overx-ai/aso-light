# 009 — Reviews Theme Classifier + Reply Queue

LLM-driven theme tagging for App Store reviews, plus the reply-queue UX that
turns those tags into a triage workflow.

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md) (review API),
[007 - MCP Integration](007-mcp-integration.md) (the `reviews.*` MCP tools).

**Related**: [000 - Architecture](000-architecture.md) §AI translation (same
Anthropic SDK pattern is used by `services/metadata/translate.py`).

---

## What it does

Each review gets an LLM-assigned `theme` and `severity` (1–5). The cache is
read on every `GET /reviews` so the badge appears in the UI without re-running
the model. The reply queue scores each review on rating + severity + recency +
whether it's already been replied to, and surfaces the highest-priority
un-handled reviews first.

| Theme | Meaning |
|---|---|
| `bug` | broken behavior, crashes, regressions |
| `feature_request` | explicit ask for a new capability |
| `praise` | positive sentiment, no actionable issue |
| `pricing` | complaints/observations about cost, subscription, IAP |
| `ux` | confusing, slow, or unpolished UX (not a hard bug) |
| `support` | user trying to reach the developer / asking a question |
| `other` | fallback when none of the above fit |

Severity rubric (per the prompt — kept here so the rule of record is in docs,
not the codebase):

| Score | Meaning |
|---|---|
| 5 | blocks core flow / data loss / crashes for many |
| 4 | serious bug / strong frustration |
| 3 | noticeable issue or moderate request |
| 2 | minor polish or nit |
| 1 | casual praise or off-topic |

---

## Architecture

```
GET /apps/:id/reviews
   ▼
ASCReviewService.list_reviews ─────► ASC API
   ▼
_serialize_review ─► ReviewOut[]
   ▼
load_theme_map(session, app_id, ids) ──► review_theme_cache (DB)
   ▼
apply_themes(items, map)  ─► populates ReviewOut.theme + .severity
   ▼
client (web UI / MCP)


POST /apps/:id/reviews/classify-themes
   ▼
ASCReviewService.list_reviews / .get_review (depending on body.review_ids)
   ▼
partition_for_classify (filters cached + bodyless)
   ▼
classify_reviews (services/reviews/themes.py) ─► Claude Haiku 4.5 forced tool call
   ▼
upsert_classifications (per-row delete-then-insert, portable to SQLite + Postgres)
   ▼
ClassifyThemesOut {classified, skipped_cached, skipped_no_body}
```

### Why a forced tool call

`services/reviews/themes.py` declares one Anthropic tool with a strict input
schema (`{classifications: [{review_id, theme, severity}, ...]}`) and forces
the model to call it via `tool_choice={"type": "tool", "name": "classify_reviews"}`.
Eliminates an entire class of fragile-prose-parsing bugs and lets
`response.content[*].input` be consumed as a typed dict.

---

## DB schema

`review_theme_cache` (table — one row per `(app_id, review_id)`).

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `app_id` | int FK→apps.id ondelete CASCADE | indexed |
| `review_id` | string(64) | ASC review id |
| `theme` | string(32) | one of the 7 themes above |
| `severity` | int | 1–5 |
| `model` | string(64) | the Anthropic model that produced it |
| `classified_at` | datetime tz | server default `now()` |

Unique on `(app_id, review_id)`; secondary index on `(app_id, theme)` for the
"give me all the bugs" filter (not yet exposed but cheap to add).

Migration: `bb7bbd4f5582_add_review_theme_cache.py`.

---

## Key files

| Layer | File |
|---|---|
| Model | `backend/app/models/review_theme.py` |
| Service (LLM + cache helpers) | `backend/app/services/reviews/themes.py` |
| Schema | `backend/app/schemas/review.py` (added `theme`, `severity`, `ClassifyThemesIn/Out`) |
| REST route | `backend/app/api/v1/reviews.py` (route at `/apps/:id/reviews/classify-themes`; `list/get` hydrate themes) |
| MCP tool | `backend/app/mcp/tools/reviews.py` (`reviews.classify_themes`) |
| Migration | `backend/alembic/versions/bb7bbd4f5582_add_review_theme_cache.py` |
| Frontend types | `frontend/src/types/index.ts` (`ReviewTheme`, `ClassifyThemesIn/Out`, `ReviewOut.theme`) |
| Frontend hook | `frontend/src/lib/hooks.ts` (`useClassifyReviewThemes`) |
| Frontend page | `frontend/src/pages/ReviewsPage.tsx` (toolbar redesign, theme column, priority sort, theme filter chips) |

---

## Reply-queue priority score

Pure client-side function in `ReviewsPage.tsx`. Documented here because it's
the actual rule for "which review do I reply to first?":

```
priority = (6 − rating) × 20             # 1★ → 100, 5★ → 20
         + severity × 10                 # 0–50
         + (response ? 0 : 30)           # un-replied bonus
         + max(0, 20 − age_days × 1.4)   # decays to 0 by day ~14
```

Sort descending. Recency boost decays linearly so a 14-day-old review
contributes nothing extra; tweak the `1.4` constant if you want a different
half-life.

---

## Operational notes

- **Server-side auto-classify cap**: `_CLASSIFY_AUTO_LIMIT = 50` in both the
  REST route and the MCP tool. Without it a single button click could blow
  out the LLM bill on apps with thousands of reviews.
- **`force=False` by default**: re-running the classify call skips rows that
  are already cached. The frontend has no UI for `force=true` yet — pass it
  manually if you want to re-classify (e.g. after improving the prompt).
- **Bodyless reviews**: rows where both `body` and `title` are empty are
  skipped (`skipped_no_body` in the response) — there's nothing for the model
  to read.
- **DB session held during the LLM call**: `classify_themes` keeps a
  `Depends(get_session)` open while Claude responds (~1–10s for a batch of
  30). Acceptable at current scale; refactor to (fetch → close → LLM →
  reopen for upsert) when concurrency rises. Same shape as
  `services/metadata/translate.py`'s `translate_with_cache`.
- **Cost**: Claude Haiku 4.5, batch of 30 reviews, costs <$0.01 per call. The
  cache means each review is paid for once.

---

## UX behaviors

`ReviewsPage.tsx`:

- **"Classify themes" button** — purple, sparkles icon. Posts `{}` to the
  endpoint (auto-mode). Tooltip shows `${classified}/${total} classified` so
  the user can see progress.
- **"Sort by priority" switch** — toggles the priority-score sort above. Off
  by default; the underlying ASC ordering (newest first) is preserved when
  the switch is off.
- **Theme filter chips** — multi-select, color-matched to the theme badge.
  Clear button appears when any chip is selected.
- **Theme column** — colored badge `Bug · 4` (severity inline). Tooltip
  exposes the severity number explicitly.
- **`N of M` counter** — the toolbar shows post-filter count vs total so you
  can see how aggressive the filter is.

---

## Limits + future work

- **Server-side priority sort** (would let users page through queues with
  thousands of items rather than client-sorting the loaded 100).
- **Per-theme reply templates** (auto-set tone: apologetic for bugs,
  appreciative for praise) — natural pair-completion of this iteration.
- **Sentiment trend** (time-series of theme volume — "bug rate spiked after
  release X") — would need a small timeseries store on top of the cache.
- **Cross-store** (Google Play, etc.) — out of scope for ASC-only project but
  candidate for `AppFollow` integration if we add that paid provider.
