---
id: 017
title: "MCP: CPP search keywords and promotional text; app preview videos synced from a directory"
status: done
priority: p1
created: 2026-10-17
updated: 2026-10-17
repo: aso-light
release:
story:
depends_on: []
conflicts_with: []
source: META_STATE bridges 013 (aso-light → iOS apps, store-promo) and 014 (hero-ai → aso-light, app-preview)
tasks: []
---

# 017 - CPP Keywords, CPP Promotional Text and App Preview Sync

> **TL;DR** — Added CPP promotional text and search keywords: `cpp_keywords_set` links only `appKeywords` ids Apple lists, warns on 4.3(b) crowded terms and is a dry run by default. App previews sync from `<dir>/<locale>/NN_*.mp4` to the listing, a CPP or a PPO treatment, each ffprobe-checked first, on the screenshot engine moved to `asset_sync.py`. Confirm the keyword id format live on Refresher.

**Prerequisites**:
- [013 - Screenshots sync from a directory](013-screenshots-sync-from-directory.md), [015 - CPP screenshots sync](015-cpp-screenshots-sync.md), [016 - Experiment screenshots sync](016-experiment-screenshots-sync.md). Their scan, plan, apply, allowlist and one-apply guard are reused, not copied.
- [docs/013 - Custom product pages](../013-custom-product-pages-and-visual-compare.md), [docs/015 - Product Page Optimization](../015-product-page-optimization.md).

## Problem
- Apple lets a custom product page (CPP) appear in organic search for keywords assigned to it (Oct 2025; 70 CPPs per app). aso-light cannot assign them, so a CPP only gets the traffic a link or Search Ads sends it. That is wrong for Refresher: the founder rejected paid-ads scaling, so its CPPs must earn organic search traffic.
- `cpp_create` and `cpp_update` take only `name` and `visible`. Promotional text is read and never written (`services/asc/cpp.py` ~351).
- Backlog B-004: app preview videos are the last asset with no upload path, on the listing, on CPPs and on treatments. A preview in slot 1 also triggers the search carousel.

## Requirements
1. **Promotional text.** `cpp_ensure_localization` accepts `promotional_text` (≤170 chars) and writes it through `PATCH /v1/appCustomProductPageLocalizations/{id}`.
   - It applies only when the CPP version is editable; otherwise the existing `CPPVersionNotEditableError` is raised.
   - Over 170 chars is a `ToolError` before any ASC call.
2. **CPP keywords.** `cpp_keywords_get {app_id, cpp_id, locale}` and `cpp_keywords_set {app_id, cpp_id, locale, keywords[], apply=false}`.
   - Use `GET`/`POST`/`DELETE /v1/appCustomProductPageLocalizations/{id}/relationships/searchKeywords`, with linkages of type `appKeywords`.
   - `set` diffs against the current set and returns `{add, remove, keep}`. It is a dry run by default.
   - T1 settles what an `appKeywords` id is and where valid ids come from. The candidate source is `GET /v1/appCustomProductPageLocalizations/{id}/searchKeywords` and the app-level keyword resources. A keyword Apple doesn't offer is reported as `error`, never invented.
   - Every keyword is checked against `CROWDED_CATEGORY_TERMS`, the 4.3(b) categories in the bible's `07-app-store/01-review-guidelines.md`: dating, flashlight, sound effects, wallpaper, timer, fortune telling, astrology, horoscope, zodiac, palm reading, tarot. A match is a `warning` row; it is not blocked.
3. **Preview sync.** `previews_sync {app_id, dir, locales?, preview_types?, apply=false}` targets the editable version. `cpp_previews_sync {…, cpp_id}` and `experiment_previews_sync {…, experiment_id, treatment_id}` target the same three parents as the screenshot syncs.
   - **Directory:** `<dir>/<locale>/NN_*.{mp4,mov,m4v}`. This is the fastlane `previews/` layout and hero-ai's `appstore-exports/previews/` export.
   - **Before any ASC call,** each file is probed with ffprobe and refused unless:
     - its duration is 15–30 s;
     - its resolution is in `PREVIEW_TYPE_BY_SIZE`: 886×1920 or 1920×886 → `IPHONE_67`; 1200×1600 or 1600×1200 → `IPAD_PRO_3GEN_129` (T1 confirms the exact `previewType` enums);
     - its video is H.264 at ≤30 fps;
     - it has a stereo audio track;
     - it is ≤500 MB.

     The ffprobe binary path is a setting.
   - **Upload:** reserve with `POST /v1/appPreviews`, run the upload operations, then commit with `PATCH uploaded=true, sourceFileChecksum`. Set `previewFrameTimeCode` from an optional `NN_*.poster.txt`, defaulting to `00:00:05:00`.
   - **Shared engine:** the generic part of `services/asc/screenshots.py` (reserve, upload, commit, MD5 skip, per-type replace as a unit, `SCREENSHOT_SYNC_ROOTS`, one apply per target, progress rows) is extracted so screenshots and previews share it. The screenshot tests stay green unchanged.
   - **Limits:** at most 3 previews per type per locale (Apple's limit). The existing editability guards apply: version, CPP and experiment states.
4. **No new secrets.** All of this uses the app's existing ASC key.

## Architecture
- **`services/asc/asset_sync.py`:**
  - the asset-kind table: `screenshot` → `appScreenshotSets`/`appScreenshots`/`screenshotDisplayType`; `preview` → `appPreviewSets`/`appPreviews`/`previewType`;
  - the engine lifted out of `screenshots.py`;
  - `screenshots.py` keeps its public names as thin wrappers, so callers and tests don't change.
- **`services/asc/previews.py`:** the ffprobe probe, `PREVIEW_TYPE_BY_SIZE` and the `PreviewSyncService` subclasses for the listing, CPP and treatment parents. These mirror `ASCVersionScreenshotService`, `CPPScreenshotService` and the treatment service.
- **`services/asc/cpp.py`:** promotional-text PATCH and the search-keywords linkage calls.
- **`mcp/tools/cpp.py`:** extends `cpp_ensure_localization`, adds `cpp_keywords_get` and `cpp_keywords_set`.
- **`mcp/tools/screenshots.py`** (or a new `previews.py` registered the same way): the three `*previews_sync` tools.
- **Constants:**
  - `CROWDED_CATEGORY_TERMS`, `PREVIEW_TYPE_BY_SIZE`, `PREVIEW_MIN_S=15`, `PREVIEW_MAX_S=30`, `PREVIEW_MAX_BYTES`, `PREVIEW_MAX_PER_TYPE=3` go in one constants place next to `DISPLAY_TYPE_BY_SIZE`.
  - `FFPROBE_BIN` goes in config.

## Edge Cases & Risks
| Case | Impact | Mitigation | Source |
|------|--------|------------|--------|
| `appKeywords` ids are not free text | `set` can't take raw words | T1 reads the schema and a live GET on Refresher's CPP; ids come only from Apple | Apple docs, 2026-10-17 |
| Preview still `PROCESSING` after commit | A re-run re-uploads | Treat a `PROCESSING` slot like the screenshot `_slot_processing` rule: skip, report | spec 013 |
| Preview from a 1290×2796 export (bible `04` says so) | Apple rejects it | The probe refuses it with "accepted: 886×1920 / 1200×1600" | Apple app preview specs |
| A crowded-category keyword on a CPP | 4.3(b) rejection risk | Warning row; the caller decides | bible 01, 2026-10-16 guidelines |
| Treatment in a running experiment | Write lands mid-test | The existing state guard from spec 016 | spec 016 |
| Refactor breaks screenshot sync | Regression on every app's listing | Screenshot tests run unchanged; the wrappers keep the public names | own analysis |

## Cross-Repo Interfaces
| Direction | Contract | Pinned version | Owner | Fallback |
|-----------|----------|----------------|-------|----------|
| consumes | hero-ai `app_preview` export layout `appstore-exports/previews/<locale>/01_preview.mp4` (hero spec 069) | hero 069 | hero-ai | fastlane `previews/<locale>/` from `make previews` |
| publishes | MCP tools `cpp_keywords_get`, `cpp_keywords_set`, `previews_sync`, `cpp_previews_sync`, `experiment_previews_sync`, and `cpp_ensure_localization.promotional_text` | 017 | aso-light | ASC web UI |

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Confirm the API: `appKeywords` id format and source, `searchKeywords` linkage bodies, `previewType` enums, `appPreviews` reserve and commit fields | dev | — | done (Apple docs JSON; live Refresher GET not run — see docs/013) | docs/013-custom-product-pages-and-visual-compare.md |
| T2 | CPP promotional text write | dev | T1 | done | backend/app/services/asc/cpp.py, backend/app/mcp/tools/cpp.py |
| T3 | CPP keywords get/set with the crowded-term warning | dev | T1 | done | backend/app/services/asc/cpp.py, backend/app/mcp/tools/cpp.py |
| T4 | Extract `asset_sync.py` from `screenshots.py`, with no behaviour change | dev | — | done | backend/app/services/asc/asset_sync.py, backend/app/services/asc/screenshots.py |
| T5 | Preview probe and the three preview sync services and tools | dev | T1, T4 | done | backend/app/services/asc/previews.py, backend/app/mcp/tools/ |
| T6 | Tests: promo text PATCH and the 170 limit; keyword diff and warning; preview probe refusals; preview reserve, upload and commit on a mocked ASC | test-writer | T2, T3, T5 | done | backend/tests/ |
| T7 | Contract test for the published tool schemas | test-writer | T5 | done (`tests/contract/test_spec_017_tools.py`) | backend/tests/ |
| T8 | `docs/PINS.md`: pin the hero 069 export layout | dev | — | done | docs/PINS.md |
| T9 | Docs: CPP keywords and previews in docs/013 and docs/015; B-004 moves to done | dev | T5 | done | docs/ |

## Acceptance Criteria
- [x] `cpp_ensure_localization` writes promotional text. Over 170 chars is refused before any call.
- [x] `cpp_keywords_set` dry run shows add/remove/keep and warns on crowded terms; apply makes the linkage calls.
- [x] `previews_sync` dry run on a directory of mixed files plans only the files that pass the probe; refusals are named.
- [x] A preview applied to a mocked CPP goes reserve → upload → commit, and the MD5 skip makes a second run a no-op.
- [x] The screenshot sync tests pass unchanged.
- [x] All tests passing (654 passed; the one failure is bug 005's pinned red test, untouched)
- [x] Code review clean
