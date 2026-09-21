---
id: 007
title: "Metadata Editor + Cross-Localization Grid + AI Translation"
status: done
created: 2026-05-05
completed: 2026-05-05
tasks: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
---

# 007 - Metadata Editor + Cross-Localization Grid + AI Translation

## Context

Phases 1-4 shipped pricing, keyword tracking, and subscription/IAP management. The single biggest gap blocking the user (a solo indie at zero downloads) from actually growing apps from inside ASO-Light is that they can't edit app metadata (title, subtitle, keywords, description, promo text, what's new) per locale — they have to bounce to App Store Connect's tab-per-locale UI for any change.

This phase closes that gap and layers on the highest-leverage cold-start tools: a cross-localization grid (with secondary-language indexing hints), bulk fan-out, Claude AI translation suggestions, and color-coded keyword highlighting on the existing Keywords page. Aso.dev validates this exact bundle as the "Console + Indie tier" that hooks indie devs daily; we already lead them on pricing — this brings us to parity on metadata, which is what drives organic visibility from zero.

## Requirements

- Per-locale CRUD for AppInfo and AppStoreVersion metadata via ASC API.
- Single-locale focused editor + all-locales grid view (mantine-datatable, GDP-sorted).
- Bulk fan-out: edit one field once → diff preview → apply to N selected locales.
- Claude AI translation: suggestion-only (never auto-apply), brand-name allowlist, char-limit-aware.
- Color-coded keyword coverage (green=title / orange=subtitle / yellow=keywords / gray=none) integrated into existing Keywords page.
- Cross-localization page: territories × indexed-locales grid, GDP-sorted, with metadata coverage status overlay.
- Preview-then-apply pattern mirroring `pricing.py`. 50-locale cap per bulk request.

## Architecture

### Pattern reuse
- ASC has no native staging — preview is synthesized server-side as a diff against the cached snapshot. Apply replays PATCHes per locale with a success/skip/error matrix mirroring `PriceApplyResponse`.
- One translation provider (Anthropic) behind an `AbstractTranslator` ABC so DeepL/OpenAI plug in later without churn.
- Color-coding is a pure function (`classify_keyword`) reused by both metadata and keywords routers.

### ASC API integration

| Tree | Endpoint | Editable when |
|------|----------|---------------|
| `appInfoLocalizations` | `/v1/appInfos/{id}/appInfoLocalizations` | Always (uses `PREPARE_FOR_SUBMISSION` AppInfo if exists, else live) |
| `appStoreVersionLocalizations` | `/v1/appStoreVersions/{id}/appStoreVersionLocalizations` | Only in editable version states; on `READY_FOR_DISTRIBUTION` only `promotionalText` mutates |

Editable version states: `PREPARE_FOR_SUBMISSION`, `READY_FOR_SUBMISSION`, `WAITING_FOR_REVIEW` (limited), `DEVELOPER_REJECTED`, `REJECTED`, `METADATA_REJECTED`. Snapshot exposes `editable_fields: list[str]` per kind so the UI greys out forbidden fields rather than letting the user discover via 409.

Field char limits: title 30, subtitle 30, keywords 100 (incl. commas), description 4000, whatsNew 4000, promotionalText 170. URLs validated. Uses existing `ASCClient._get_all_pages()` and 150ms throttle.

### New backend modules

- `backend/app/models/metadata.py`:
  - `AppMetadataLocalization` UNIQUE`(app_id, kind, locale)` — snapshot cache
  - `AppMetadataState` — one row per app: editable_version_id, state, editable_fields_json, last_synced_at
  - `MetadataTranslationCache` — bounds Anthropic spend
- `backend/app/services/metadata/`:
  - `client.py` → `ASCMetadataService` (read + write)
  - `snapshot.py` → `MetadataSnapshotService.sync_app(app_id)` (idempotent)
  - `bulk.py` → `BulkMetadataService.preview/apply`
  - `validation.py` → char limits + URL validators
  - `coloring.py` → pure `classify_keyword(...)`
  - `translate.py` → `AbstractTranslator` ABC + `AnthropicTranslator`
- `backend/app/api/v1/metadata.py` — new router (mounted at `/apps`)
- `backend/app/api/v1/_deps.py` — extract `_get_verified_app` + `_get_asc_client_for_app` from `pricing.py` (currently duplicated)

### New endpoints (`/apps/...`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{app_id}/metadata` | Cached snapshot + editable-state flags |
| POST | `/{app_id}/metadata/sync` | Pull from ASC, upsert |
| POST | `/{app_id}/metadata/{kind}/{locale}` | Create locale row |
| PATCH | `/{app_id}/metadata/{kind}/{locale}` | Update locale |
| DELETE | `/{app_id}/metadata/{kind}/{locale}` | Delete locale |
| POST | `/{app_id}/metadata/bulk/preview` | Diff list |
| POST | `/{app_id}/metadata/bulk/apply` | Per-locale success/skip/error matrix; cap 50 |
| POST | `/{app_id}/metadata/translate` | Suggestions only; never writes |
| GET | `/{app_id}/metadata/keyword-coverage` | tracked keyword × locale → which field |
| GET | `/keywords/cross-localization-grid` | Cross-loc data joined with GDP for default sort |

### Frontend additions

- `frontend/src/pages/MetadataPage.tsx` — mirrors `PricingPage.tsx`. Tabs: "Single locale" (focused editor + char-counter + keyword-coverage badges + Translate button) | "All locales (grid)" (mantine-datatable, row-click → drawer). `<BulkFanoutDrawer>` from grid toolbar.
- `frontend/src/pages/CrossLocalizationPage.tsx` — territories × locales grid, GDP-sorted, indexed-locale chips with metadata coverage overlay.
- Extend `KeywordsPage.tsx` with "Coverage" column (colored dots per locale group). Pure additive.
- Append hooks to `frontend/src/lib/hooks.ts`: `useAppMetadata`, `useSyncMetadata`, `useUpdateLocale`, `useCreateLocale`, `useDeleteLocale`, `usePreviewBulkMetadata`, `useApplyBulkMetadata`, `useTranslateMetadata`, `useKeywordCoverage`, `useCrossLocalizationGrid`.
- Routes in `App.tsx` + nav links in `Layout.tsx`.

### Misc
- Add `gdp_per_capita_usd: Mapped[float | None]` to `Territory`; seed in `app/data/territories.py`. Used for default GDP-sort.
- Add `anthropic` dependency to `backend/pyproject.toml`. `ANTHROPIC_API_KEY` via pydantic-settings env var.
- Reuse existing `cross_localization.py` — its keys are the canonical locale set.

## Edge Cases & Risks

| Case | Impact | Mitigation | Source |
|------|--------|------------|--------|
| Apple BCP-47 locale codes (`zh-Hans`, `pt-BR`, `nb`, `el`) differ from generic ISO | Bad data into ASC → 400s | Use `cross_localization.py` keys as canonical set; reject unknown server-side | brainstorm |
| Version state machine — most fields unmutable on `READY_FOR_DISTRIBUTION` | UX surprise via 409 | Snapshot exposes `editable_fields`; UI greys out forbidden fields | brainstorm |
| Translation hallucinates brand names / over-translates keyword commas | Bad metadata | Prompt with field kind + char limit + brand allowlist; post-process keywords (split, dedupe, hard-truncate 100); suggestion-only, never auto-apply | brainstorm |
| Anthropic cost spike on bulk translate | $$ surprise | Translation cache + per-app monthly call counter; soft cap ~500/month with override | brainstorm |
| Concurrent edits across two browser tabs | Lost write | Last-write-wins + "stale snapshot" warning if `synced_at` > 5 min when editor opens | brainstorm |
| "Secondary indexing" claim (es-MX → US) — Apple has never published a formal table; behavior shifts | Misleading users | Flag in UI as "community-derived, last verified [date]"; link to source file | brainstorm |
| Bulk fan-out blast radius | Mass-corrupt metadata | 50-locale cap per request; preview required before apply (UI flow) | brainstorm |

## Tasks

| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Add `gdp_per_capita_usd` to Territory + seed + migration | dev | — | done | `backend/app/models/territory.py`, `backend/app/data/territories.py`, `backend/app/data/seed.py`, `backend/alembic/versions/002_add_gdp_per_capita_to_territories.py` |
| T2 | Extract `_get_verified_app` + `_get_asc_client_for_app` to shared deps | dev | — | done | `backend/app/api/v1/_deps.py` (new), `backend/app/api/v1/pricing.py`, `backend/app/api/v1/keywords.py` |
| T3 | New SQLAlchemy models + Alembic migration | dev | — | done | `backend/app/models/metadata.py` (new), `backend/app/models/__init__.py`, `backend/alembic/versions/003_add_metadata_tables.py` |
| T4 | Pydantic schemas + char-limit validators | dev | T3 | done | `backend/app/schemas/metadata.py`, `backend/app/services/metadata/validation.py` |
| T5 | `ASCMetadataService` read endpoints | dev | — | done | `backend/app/services/metadata/__init__.py`, `backend/app/services/metadata/client.py` |
| T6 | `ASCMetadataService` write endpoints + state-machine guard | dev | T5 | done | `backend/app/services/metadata/client.py` |
| T7 | `MetadataSnapshotService` (sync + upsert) | dev | T3, T5 | done | `backend/app/services/metadata/snapshot.py` |
| T8 | Pure `coloring.classify_keyword` + tests | dev | — | done | `backend/app/services/metadata/coloring.py`, `backend/tests/services/metadata/test_coloring.py` (18 tests, all pass) |
| T9 | `BulkMetadataService` preview/apply | dev | T4 | done | `backend/app/services/metadata/bulk.py` |
| T10 | `AnthropicTranslator` + ABC + cache | ml-engineer | T3 | done | `backend/app/services/metadata/translate.py`, `backend/app/core/config.py`, `backend/pyproject.toml` (anthropic 0.98.1) |
| T11 | `metadata.py` router wiring all endpoints | dev | T2, T6, T7, T9, T10 | done | `backend/app/api/v1/metadata.py`, `backend/app/api/v1/__init__.py` (10 routes) |
| T12 | Frontend hooks + types | dev | T11 | done | `frontend/src/lib/hooks.ts`, `frontend/src/types/index.ts` (10 hooks) |
| T13 | `MetadataPage` + sub-components | dev | T12 | done | `frontend/src/pages/MetadataPage.tsx`, `frontend/src/components/metadata/{MetadataHeader,LocaleEditor,MetadataGrid,BulkFanoutDrawer,CharLimitCounter,KeywordCoverageBadge,EmptyState,localeLabel,fieldConfig}` |
| T14 | `CrossLocalizationPage` | dev | T12 | done | `frontend/src/pages/CrossLocalizationPage.tsx` |
| T15 | Keyword coverage column on KeywordsPage | dev | T12 | done | `frontend/src/pages/KeywordsPage.tsx`, `frontend/src/components/metadata/KeywordCoverageDots.tsx` |
| T16 | Routing + nav links | dev | T13, T14 | done | `frontend/src/App.tsx`, `frontend/src/components/AppNavItem.tsx` |
| T17 | Env docs + README + CLAUDE.md update | ops | T10 | done | `backend/.env.example`, `README.md`, `CLAUDE.md` |

Parallel groups (after T1+T2+T3): T4, T5, T8, T10. Then T6 → T7/T9. Then T11 → T12 → {T13, T14, T15} → T16. T17 in parallel after T10.

## Acceptance Criteria

- [ ] `GET /apps/{id}/metadata` returns both AppInfo + AppStoreVersion locales with `editable_fields` flags
- [ ] Single-locale edit round-trips to ASC and verifies in App Store Connect within 30s
- [ ] All-locales grid loads with default GDP sort (US/CN/JP/DE top)
- [ ] Bulk fan-out preview shows accurate diff; apply returns success/skip/error matrix; capped at 50 locales
- [ ] Translation suggestions appear with field-kind + char-limit awareness; never auto-applied; cached per `(app_id, src, tgt, source_hash, field_kind)`
- [ ] Keywords page shows colored coverage dots per locale; updates after metadata change + re-sync
- [ ] Cross-localization grid GDP-sorted with secondary-indexing chips visible (e.g. es-MX on BR/US/CO/AR/CL/PE)
- [ ] Forbidden fields greyed out when version state is `READY_FOR_DISTRIBUTION` (only `promotionalText` editable)
- [ ] Char-limit validator enforced server-side AND in UI
- [ ] All ASC mutations verify `app.credential_id → credential.user_id == current_user_id`
- [ ] All tests passing, code review clean

## Implementation Notes (post-merge)

- **Quality gate (go-review)** found and fixed 5 issues: cache-invalidation gap on metadata mutations (Keywords coverage went stale), silent exception swallowing in `BulkMetadataService.apply` (now logs `ASCAPIError` + falls through `logger.exception`), Save button enabled past char limit, lost exception cause chain (4 spots — added `from exc`), bulk drawer's editableSet identity churn.
- **Pre-existing test failures** in `test_preview_endpoint.py` and `test_preview_pricing.py` (missing `pytest-asyncio` markers) are NOT caused by this phase — confirmed by stashing changes; failed before. Backlog: add the markers or update pyproject.toml asyncio mode.
- **Translation cache index**: composite `ix_metadata_translation_cache_app_created` (app_id, created_at) added by T3 — covers the rolling-30-day cap query.
- **Editable-state surfacing**: backend computes `editable_fields: list[str]` per app and the frontend uses it to grey out forbidden fields; on `READY_FOR_DISTRIBUTION` only `promotional_text` remains editable.
- **Translation contract**: suggestions only — never auto-applied. Per-app rolling 30-day soft cap = 500. Default model: `claude-haiku-4-5-20251001` for cost.
- **Blocked actions on first run**: a fresh app shows `204 No Content` from `GET /metadata` and the UI prompts the user to click "Sync from ASC" before any edits are possible.
