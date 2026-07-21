# 006 - Metadata Editor + Cross-Localization + AI Translation

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md), [003 - Keyword Analysis](003-keyword-analysis.md), [004 - Localization Management](004-localization-management.md)
**Related**: [001 - Pricing System](001-pricing-system.md) (mirror preview-then-apply pattern)
**Spec**: [007 - Metadata Editor + Cross-Loc](specs/007-metadata-editor-and-cross-loc.md)

## Overview

Edit App Store metadata (title, subtitle, keywords, description, promotional text, what's new, marketing/support/privacy URLs) per-locale without leaving ASO-Light. Bulk fan-out a field across many locales with diff preview. Optional one-click translation via Anthropic Claude. Cross-Localization grid surfaces Apple's secondary-language indexing (e.g. `es-MX` content surfaces in BR/AR/CL/CO/PE App Stores). Color-coded keyword coverage on the existing Keywords page shows where each tracked keyword lives across locales.

This complements [004 - Localization Management](004-localization-management.md) (which covers subscription/IAP display-name localizations) by handling **app-level** metadata.

## Out of Scope (deferred to later phases)

- ~~A/B testing (Product Page Optimization, Custom Product Pages)~~ — both now built: Custom Product Pages in [013](013-custom-product-pages-and-visual-compare.md), Product Page Optimization in [015](015-product-page-optimization.md)
- Apple Search Ads intelligence (Share of Voice, Auction Insights)
- DeepL / OpenAI / Gemini translation providers (Anthropic only for now; `AbstractTranslator` ABC is plug-in ready)
- MCP server exposing metadata read/write to Claude Code (planned future phase)
- Background scheduler / alerting on rank or rating drops

## Apple's Constraints

The App Store splits text metadata into two trees with different mutability rules:

| Tree | Endpoint | Editable when |
|------|----------|---------------|
| AppInfo localizations | `/v1/appInfos/{id}/appInfoLocalizations` | Always (uses `PREPARE_FOR_SUBMISSION` AppInfo if present, else live) |
| AppStoreVersion localizations | `/v1/appStoreVersions/{id}/appStoreVersionLocalizations` | Only in editable version states; on `READY_FOR_DISTRIBUTION` only `promotionalText` mutates |

**Editable version states**: `PREPARE_FOR_SUBMISSION`, `READY_FOR_SUBMISSION`, `WAITING_FOR_REVIEW` (limited), `DEVELOPER_REJECTED`, `REJECTED`, `METADATA_REJECTED`.

**Field char limits** (enforced both server- and client-side):

| Field | Limit | Tree |
|-------|-------|------|
| name | 30 | AppInfo |
| subtitle | 30 | AppInfo |
| privacy_policy_url | 1024 (URL) | AppInfo |
| description | 4000 | Version |
| keywords | 100 (incl. commas) | Version |
| promotional_text | 170 | Version |
| whats_new | 4000 | Version |
| marketing_url | 1024 (URL) | Version |
| support_url | 1024 (URL) | Version |

Backend exposes `editable_fields: list[str]` per app via `GET /apps/{id}/metadata` so the UI greys out forbidden fields rather than discovering 409s on save.

## Backend

### Models — `backend/app/models/metadata.py`

| Model | Purpose |
|-------|---------|
| `AppMetadataLocalization` | Snapshot cache of per-locale metadata pulled from ASC. UNIQUE`(app_id, kind, locale)`, where `kind ∈ {'app_info','version'}`. |
| `AppMetadataState` | One row per app: `editable_version_id`, `editable_version_state`, `app_info_id`, `editable_fields_json`, `last_synced_at`. |
| `MetadataTranslationCache` | Bounds Anthropic spend. UNIQUE`(app_id, source_locale, target_locale, source_hash, field_kind)`. Composite index `ix_metadata_translation_cache_app_created` covers the rolling-30-day cap query. |

`Territory.gdp_per_capita_usd: Mapped[float | None]` added (seeded with World Bank 2024 PPP figures) to drive default GDP-sort on the Cross-Localization grid.

Migration chain: `001_preset_config → 002_territory_gdp → 003_metadata_tables`.

### Service modules — `backend/app/services/metadata/`

| File | Purpose |
|------|---------|
| `client.py` | `ASCMetadataService` — wraps AppInfo + AppStoreVersion read/write endpoints. State-machine guard refuses non-`promotionalText` mutations on `READY_FOR_DISTRIBUTION` (raises `MetadataNotEditableError` → 409). Exports `EDITABLE_VERSION_STATES`, `READ_ONLY_VERSION_STATES_PROMO_ONLY`, `PROMO_ONLY_FIELDS_ON_LIVE` constants. |
| `snapshot.py` | `MetadataSnapshotService.sync_app(app)` — pulls both trees from ASC, upserts into snapshot tables, computes `editable_fields`, deletes stale rows. Idempotent. Returns `SnapshotResult` dataclass. Defensive AppInfo state field name handling (`appStoreState` vs `state`). |
| `bulk.py` | `BulkMetadataService.preview/apply` — pure logic over snapshot + char-limit validation. `apply()` replays per-locale PATCHes and returns success/skip/error matrix mirroring `PriceApplyResponse`. `force=True` overrides only **soft** skips (unchanged value, state-machine guess) — never **hard** skips (char overflow, missing localization row). Logs `ASCAPIError` warnings + `logger.exception` for unexpected failures. |
| `validation.py` | `FIELD_CHAR_LIMITS`, `URL_FIELDS`, `ALL_FIELDS`, `validate_field()`, `char_overflow()`, `is_valid_url()`. Reused by schemas, bulk service, and the AI translator's char-limit awareness. |
| `coloring.py` | Pure `classify_keyword(keyword, name, subtitle, keywords_field) -> "title"\|"subtitle"\|"keywords"\|"none"`. Precedence: title > subtitle > keywords. Comma-token exact match for keywords field. Reused by both metadata router (coverage endpoint) and frontend. 18 unit tests. |
| `translate.py` | `AbstractTranslator` ABC + `AnthropicTranslator` (default model: `claude-haiku-4-5-20251001`). Field-aware system prompt (char limit + brand allowlist). Keywords-field post-processing (split commas, dedupe, lowercase, hard-truncate 100). `translate_with_cache()` enforces rolling 30-day soft cap (default 500/app) via `MetadataTranslationCache`. Translations are **suggestion-only** — never auto-applied. |

### API endpoints

All under `/api/v1/apps/{app_id}/metadata/*` (mounted via metadata router) plus one global keywords endpoint. Every per-app route enforces `_get_verified_app(app_id, user_id, session)` from `backend/app/api/v1/_deps.py` (extracted from `pricing.py` + `keywords.py` during this phase).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/apps/{app_id}/metadata` | Cached snapshot (both trees) + `state.editable_fields`. Returns 204 if never synced. |
| POST | `/apps/{app_id}/metadata/sync` | Pull from ASC → upsert → return snapshot |
| POST | `/apps/{app_id}/metadata/{kind}/{locale}` | Create new locale row (requires prior sync — 409 otherwise) |
| PATCH | `/apps/{app_id}/metadata/{kind}/{locale}` | Update single locale; updates snapshot row in-place |
| DELETE | `/apps/{app_id}/metadata/{kind}/{locale}` | Delete locale + remove snapshot row |
| POST | `/apps/{app_id}/metadata/bulk/preview` | `{field, value, target_locales[]}` → diff list with overflow + skip reasons |
| POST | `/apps/{app_id}/metadata/bulk/apply` | `{field, value, target_locales[], force?}` → per-locale success/skip/error matrix; cap 50 |
| POST | `/apps/{app_id}/metadata/translate` | `{source_locale, target_locales[], fields[]}` → suggestions only; never writes |
| GET | `/apps/{app_id}/metadata/keyword-coverage` | tracked-keyword × locale → which field. Avoids N+1 via `selectinload(KeywordTracking.keyword)`. |
| GET | `/keywords/cross-localization-grid` | Static cross-loc data joined with `Territory.gdp_per_capita_usd` for default sort |

Error mapping:
- `MetadataNotEditableError` → 409
- `TranslationQuotaExceededError` → 429
- `ANTHROPIC_API_KEY` not set on `/translate` → 503 ("AI translation not configured")
- `ASCAPIError` escapes → 502 ("ASC API error" — never leaks raw error per CLAUDE.md)
- All re-raises use `from exc` to preserve cause chain

## Frontend

### Pages

- `frontend/src/pages/MetadataPage.tsx` — Mantine `Tabs`: "Single locale" (focused `LocaleEditor`) | "All locales (grid)" (mantine-datatable, row-click → drawer). `BulkFanoutDrawer` from grid toolbar. Empty state on first load prompts "Sync from ASC".
- `frontend/src/pages/CrossLocalizationPage.tsx` — Pivot table: rows = territories, columns = indexed locales. GDP-sorted by default. Green dot = locale has metadata; blue dot = indexed but empty. Disclaimer surfaces "community-derived data" caveat.

### Components — `frontend/src/components/metadata/`

| Component | Purpose |
|-----------|---------|
| `MetadataHeader.tsx` | App name, "Sync from ASC" button, editable-state badge (green/yellow/gray), relative-time last-sync chip |
| `LocaleEditor.tsx` | Single-locale form with App Info + Version columns. Per-field `<CharLimitCounter>`. Inline "Translate from..." button → Claude suggestions as clickable chips (cached → gray, fresh → blue). Read-only badge when field not in `editable_fields`. Save disabled while pristine OR over char limit. |
| `MetadataGrid.tsx` | mantine-datatable: rows = locales, columns = name/subtitle/keywords/promo (truncated 60ch). Row click → switches to single-locale tab. |
| `BulkFanoutDrawer.tsx` | Right-side 50% drawer. Field/source/value/targets form. Auto-fills value from source locale. Preview table → diff inspection → Apply. 50-locale cap enforced client-side too. |
| `CharLimitCounter.tsx` | `{value.length}/{limit}` chip, colored by state |
| `KeywordCoverageBadge.tsx` | Shows colored dots per tracked keyword present in current locale |
| `KeywordCoverageDots.tsx` | Used on KeywordsPage Coverage column — per-keyword dots across all locales, hover popover lists locale + placement |
| `EmptyState.tsx` | First-run prompt with sync button |
| `localeLabel.ts` | `localeLabel(locale)` → display name (e.g. `'zh-Hans' → 'Chinese (Simplified)'`). ~40-locale static map matching `cross_localization.py`'s set, with `Intl.DisplayNames` fallback. |
| `fieldConfig.ts` | Char limits + relative-time helpers |

### Hooks — appended to `frontend/src/lib/hooks.ts`

10 hooks: `useAppMetadata`, `useSyncMetadata`, `useCreateLocale(appId)`, `useUpdateLocale(appId)`, `useDeleteLocale(appId)`, `usePreviewBulkMetadata(appId)`, `useApplyBulkMetadata(appId)`, `useTranslateMetadata(appId)`, `useKeywordCoverage(appId)`, `useCrossLocalizationGrid()`.

Mutation hooks are factory-style (`useUpdateLocale(appId)`) following the existing `useCreateSubscription(appId, groupId)` pattern. Cache invalidation routed through a single `invalidateMetadataDerived(qc, appId)` helper that hits both `useAppMetadata` and `useKeywordCoverage` — keeping the Keywords page Coverage column fresh after metadata edits.

### Routes

- `apps/:id/metadata` → `MetadataPage`
- `apps/:id/cross-localization` → `CrossLocalizationPage`

Wired into the per-app sub-nav in `frontend/src/components/AppNavItem.tsx` with `IconFileDescription` (Metadata) and `IconLanguage` (Cross-Loc).

## Data Flow

```
User edits subtitle in MetadataPage
       │
       ▼
PATCH /apps/{id}/metadata/app_info/en-US        ← schema + char-limit validation
       │
       ▼
ASCMetadataService.update_app_info_localization  ← state-machine guard
       │
       ▼
Apple ASC API  →  PATCH /v1/appInfoLocalizations/{loc_id}
       │
       ▼
Update snapshot row (synced_at = now)
       │
       ▼
invalidateMetadataDerived → re-fetch app metadata + keyword coverage
       │
       ▼
KeywordsPage Coverage column dots update
```

## Translation Flow

```
User clicks "Translate from en-US" on de-DE row
       │
       ▼
POST /apps/{id}/metadata/translate
       │
       ▼
For each (target_locale, field):
  source_hash = sha256(en-US value)
  cache lookup → return cached if hit
  rolling-30-day count check → 429 if cap exceeded
  AnthropicTranslator.translate(text, src, tgt, field_kind, brand_allowlist)
    ↳ field-aware system prompt (char limit + brand preserve list)
    ↳ Claude Haiku 4.5
    ↳ keywords field: post-process (split commas, dedupe, hard-truncate 100)
  cache row insert
  return suggestion
       │
       ▼
UI shows clickable chip — user accepts → fills draft → user saves manually
```

Translations are **never auto-applied**. The router has no path that writes a translation directly to ASC.

## Cross-Localization (secondary indexing)

`CROSS_LOCALIZATION_DATA` in `backend/app/services/keywords/cross_localization.py` encodes which locales Apple indexes into which storefronts (e.g. `es-MX` indexes into `BR/AR/CL/CO/PE/MX`). This is **community-derived** — Apple has never published a formal table. The `CrossLocalizationPage` surfaces a disclaimer chip and links to the source file.

For an indie growing from zero downloads: filling secondary locales is high-leverage. Adding `es-MX` content surfaces those keywords in 6 storefronts; adding `en-US` surfaces in ~50.

## Settings

`ANTHROPIC_API_KEY` in `backend/.env` — optional. Without it, the `/translate` endpoint returns 503 and the "Translate from..." button is gracefully disabled in the UI.

## Verification Flow (end-to-end on your own apps)

1. `make dev`
2. Apps → your app → Metadata → "Sync from ASC"
3. Edit `subtitle` in `en-US` → save → confirm change in App Store Connect web within 30 s
4. "All locales" tab → confirm GDP-sorted (US/CN/JP/DE top)
5. Bulk fan-out: subtitle, source `en-US`, targets `en-GB,en-AU,en-CA` → Preview → Apply → 3 successes
6. On empty `de-DE` row, click "Translate from en-US" → Claude suggestion appears as chip → accept → save
7. Keywords page → for a keyword in your `en-US` title, confirm green dot under "en-US" column
8. Cross-Localization page → confirm `es-MX` chip on BR/US/CO/AR/CL/PE rows
9. Negative test: edit `description` while version state is `READY_FOR_DISTRIBUTION` → field is greyed out + tooltip explains

## Known Limitations / Backlog

- `MetadataSnapshotService` makes 2 ASC list calls when no editable version exists (rare path; consider caching).
- `AbstractTranslator.model_name` not yet a formal property — `getattr(translator, "_model", "unknown")` reaches into impl. Formalize when DeepL/OpenAI plug in.
- Pre-existing `pytest-asyncio` marker issue in `test_preview_endpoint.py` and `test_preview_pricing.py` (unrelated to this phase) — backlog T-024.

## See Also

- [Spec 007](specs/007-metadata-editor-and-cross-loc.md) — Implementation plan with full task breakdown
- [002 - ASC Integration](002-asc-integration.md) — `ASCClient`, JWT auth, rate limiter
- [003 - Keyword Analysis](003-keyword-analysis.md) — Cross-localization data, KeywordTracking model
- [004 - Localization Management](004-localization-management.md) — Subscription/IAP localization (sibling system)
