# 000 - Tasks

## Active

| # | Task | Area | Status | Priority | Notes |
|---|------|------|--------|----------|-------|
| T-006 | Apple Search Ads API integration (keyword popularity) | Keywords | pending | high | Requires Apple Search Ads account |
| T-007 | Background scheduler for index + ranking refresh | Backend | pending | medium | APScheduler or asyncio tasks |
| T-009 | Code split frontend bundle (1.1MB) | Frontend | pending | low | Dynamic imports for route chunks |
| T-024 | Fix pre-existing pytest-asyncio markers | Backend | pending | low | `test_preview_endpoint.py`, `test_preview_pricing.py` — surfaced during Phase 5 quality gate |

## Completed

| # | Task | Area | Completed | Notes |
|---|------|------|-----------|-------|
| T-001 | Backend scaffolding (FastAPI, config, DB, Makefile) | Backend | 2026-03-23 | |
| T-002 | Frontend scaffolding (React, Vite, Mantine v8, router) | Frontend | 2026-03-23 | |
| T-003 | SQLAlchemy models + territory seed data (202 territories) | Backend | 2026-03-23 | |
| T-004 | Auth API + ASC credential management + ASC client | Backend | 2026-03-23 | |
| T-005 | Frontend auth pages + credential page + dashboard | Frontend | 2026-03-23 | |
| T-006 | ASC pricing service + pricing API endpoints | Backend | 2026-03-23 | |
| T-007 | Price calculators (PPP/BigMac/Netflix/Spotify/FixedPayout) | Backend | 2026-03-23 | |
| T-008 | Economic index fetchers + refresh service | Backend | 2026-03-23 | |
| T-009 | Frontend pricing UI (PriceGrid, MultiplierPanel, PriceDiff) | Frontend | 2026-03-23 | |
| T-010 | Price presets CRUD + Excel/CSV export/import | Full-stack | 2026-03-23 | |
| T-011 | Keyword analysis (iTunes hints, rank tracking, cross-localization) | Full-stack | 2026-03-23 | |
| T-012 | Project init (git, deps, .env, integration tests) | DevOps | 2026-03-23 | bcrypt fixed |
| T-013 | ASC rate limiter (150ms throttle + 429 backoff) | Backend | 2026-04-12 | Global lock, 6 retries |
| T-014 | Price point filesystem cache | Backend | 2026-04-12 | Per-territory JSON, async I/O |
| T-015 | Price safety limits (+20%/-25%) | Backend | 2026-04-12 | Skips territories exceeding threshold |
| T-016 | Localization management (subs + IAPs) | Full-stack | 2026-04-12 | Bulk sync, JSON import, v2 API for IAPs |
| T-017 | Review screenshot upload (subs + IAPs) | Full-stack | 2026-04-12 | 3-step flow, _put_binary for S3 |
| T-018 | Manual price pins | Full-stack | 2026-04-12 | Pin toggle, resolve to nearest Apple tier |
| T-019 | IAP full pricing workflow | Full-stack | 2026-04-12 | Mirrors subs: sync, preview, apply, pins |
| T-020 | GDP-bracket pricing strategy (spec 005) | Full-stack | 2026-04-30 | 4 absolute tiers + World Bank GDP data; safety band → ±50% |
| T-021 | App availability management page | Full-stack | 2026-04-30 | Per-territory availability editor |
| T-022 | Subscription management (groups + subs + group locs + intro offers) | Full-stack | 2026-05-01 | Spec 006; submit-for-review remains manual |
| T-023 | Metadata Editor + Cross-Loc + AI translation (Phase 5) | Full-stack | 2026-05-05 | Spec 007; 17 sub-tasks; `AnthropicTranslator` (Haiku 4.5); cap 500/app/30d; suggestion-only |
| T-010 | Alembic migrations (replace create_all in dev) | Backend | 2026-05-09 | Startup now upgrades via Alembic before seeding territories |
| T-025 | Bulk locale creation + keyword-research fixes (spec 012) | Backend | 2026-08-24 | R1 `create_missing` on bulk metadata (unblocks 31-locale fan-outs); R2 `X-Apple-Store-Front` header — `keywords_suggestions` had silently returned `[]` since day one; R3 real `keyword_intel_*` MCP tools, 2 mislabeled aliases deleted; R5 single `FIELD_CHAR_LIMITS` + retry-then-raise instead of mid-word truncation; R6 search-term report app-scoped. 331 → 347 tests |
| T-026 | Main product-page screenshots over MCP (spec 010) | Backend | 2026-08-24 | `screenshots_list` / `_upload` / `_delete`; `delete_screenshot` / `delete_screenshot_set` are the first deletes in the screenshot service layer. `screenshots_list` is the resume/repair primitive for an interrupted bulk run |

## Backlog

| # | Task | Area | Priority | Notes |
|---|------|------|----------|-------|
| B-001 | Google Play price management | Backend | low | Double scope, not in MVP |
| B-002 | DeepL / OpenAI / Gemini translation providers | Backend | low | Anthropic shipped in Phase 5; ABC ready — formalize `model_name` property when 2nd provider lands |
| B-003 | In-App Events management | Backend | low | Phase 6+ |
| B-004 | App preview videos (`appPreviewSets`) | Backend | low | Review + CPP + PPO + main-listing screenshots all shipped (T-017, spec 013, spec 015, spec 010). Preview *videos* are the remaining gap — same set/asset shape, so `services/asc/screenshots.py` generalizes |
| B-014 | Hoist `asc_tool_error` into `app/mcp/context.py` | Backend | low | Defined identically in `mcp/tools/cpp.py` + `mcp/tools/screenshots.py`, a third variant in `experiment.py`, atop ~20 inline `except ASCAPIError → ToolError`. **`screenshots.py`'s copy is a superset** (also maps `VersionNotEditableError`) — a naive hoist loses that. Deferred from spec 012 as partial-DRY churn |
| B-015 | `screenshots_list` reports `complete=True` for a version with zero screenshot sets | Backend | medium | No sets → no display types → no gaps → the "is this submittable?" flag says yes. `total_screenshots: 0` is the only tell. Either require `display_types` or infer required device families |
| B-016 | `run_providers` continues on a poisoned session | Backend | medium | A provider raising a DB error leaves the session unusable, so every later `upsert_intel` fails too. Pre-existing (inherited verbatim from the REST route); a savepoint per provider fixes it |
| B-005 | Review management (read + reply + sentiment) | Backend | low | Recommended Phase 7 |
| B-011 | Competitor Spy (reverse-keyword lookup) | Full-stack | high | Recommended Phase 6; cold-start growth tool |
| B-012 | Keywords Explorer (autocomplete chains, related queries) | Full-stack | high | Recommended Phase 6 |
| B-013 | MCP server exposing metadata read/write to Claude Code | Backend | medium | Recommended Phase 8; depends on Phase 5 metadata layer |
| B-006 | Third-party keyword API (AppTweak/data.ai) | Backend | medium | Optional premium tier |
| B-007 | Docker Compose for full stack | DevOps | medium | Prod deployment |
| B-008 | Admin panel for index data management | Frontend | low | Manual override of Netflix/Spotify data |
| B-009 | Email notifications for rank changes | Backend | low | Alerting system |
| B-010 | Subscription price equalization (Apple's cross-territory sync) | Backend | high | `GET /v1/subscriptionPricePoints/{id}/equalizations` |
