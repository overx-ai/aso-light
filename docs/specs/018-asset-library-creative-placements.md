---
id: 018
title: "MCP: App Store asset library — upload creative assets and place them on CPPs, treatments and versions"
status: approved
priority: p1
created: 2026-10-18
updated: 2026-10-18
repo: aso-light
release:
story:
depends_on: []
conflicts_with: []
tasks: []
source: META_STATE bridge 017 (aso-light → asset library); founder, 2026-10-18 ("it's new capability in asc. should we try it as well?")
---

# 018 - Asset library: creative-asset placements

> **TL;DR** — aso-light will upload App Store creative assets (header 21:9, search results 3:2, universal 16:9) to Apple's asset library and place them per localization on a CPP, a PPO treatment or an editable version. It syncs from blessing's export directory and is a dry run by default. Placement types come from Apple's reference data, never hard-coded. Refresher's organic CPP is the first target.

## Requirements
- **Founder, 2026-10-18:** try the new header and search-result assets. The pilot goes on Refresher's organic CPP:
  - version 2.2.0 is live and takes no placements;
  - the "Base vs Poster" PPO test (Apple's A/B test of the store listing) runs until 2027-01-03, and default-page art would change its base arm.
- **Apple facts** (App Store Connect API 4.5.1, fetched 2026-10-18):
  - **Upload:** `GET /v1/apps/{id}/assetLibrary`, then reserve with `POST /v1/appAssetLibraryImages` or `/appAssetLibraryVideos`, with category `CREATIVE_ASSETS`, which can't be changed later. PUT the upload operations, then commit with `uploaded: true`.
  - **Place:** `POST /v1/appAssetLibraryPlacements` with `placementType`, `placementGroup`, the asset and the target localization.
  - **Placement types and groups:** read at runtime from `GET /v1/appAssetLibraryRefData` (`placementTypes`, `placementProfileGroups`). Apple adds values without an API version change. Today: `PRODUCT_PAGE_HEADER_ASSET`, `APP_STORE_SEARCH_RESULTS_ASSET`.
  - **Editable only:** a placement can be created only while its parent surface is editable. It is reviewed with that parent (CPP version, treatment, app version).
  - **Specs:**
    - header 3840×1646;
    - search 3:2, 1920×1280 to 3840×2560;
    - universal 16:9 PNG 5244×2950;
    - JPEG or PNG with no alpha;
    - video in the same ratios, 5–30 s, 30/60 fps, .mov/.m4v/.mp4.
- **MCP tools:**

  | Tool | Does |
  |---|---|
  | `creative_assets_refdata(app_id)` | Placement types, groups and limits, as Apple returns them |
  | `creative_assets_list(app_id, target)` | The current placements on a target, per localization |
  | `creative_assets_sync(app_id, target, dir, apply=false)` | Reads `<dir>/<locale>/creative/{header,search,universal}.{png,jpg,mov,m4v,mp4}`; dry run by default |
  | `creative_assets_delete(app_id, placement_id)` | Removes one placement |

  - `target` is one of `{cpp: id}`, `{treatment: id}` or `{version: id}`.
  - The sync's dry run reports per locale: the file, its probed size, the placement type and what would change.
  - The sync is idempotent. A matching asset (same checksum) is not re-uploaded, and a placement that already exists is kept.
  - It refuses a non-editable target before any upload.
- **Probe first:**
  - images: size, ratio and alpha, with Pillow;
  - video: ratio, duration and fps, with the ffprobe helper from spec 017;
  - a refused file is never uploaded.

## Architecture
- **Upload:** reuse the reserve → PUT → commit flow in `services/asc/asset_sync.py:upload_asset`. Generalise its relationship (`asset_set`) to the library: the reservation relates to the app's asset library, not a set. The library commit needs no checksum, so it stays optional.
- **New service** `services/asc/asset_library.py`:
  - ref data, cached per process;
  - upload;
  - place, list and delete.
- **Sync engine:** follow `LocalizationAssetService` and `ExportScan` (spec 013/017), with the same `resolve_sync_dir` and `within_sync_roots` guards.
- **Target editability:** reuse `EditableVersion` / `NotEditableError`. For a CPP, use the CPP version state; for a treatment, the experiment state.
- **Tools** live in `mcp/tools/creative.py` and follow `mcp/tools/previews.py`, including the dry-run default and the error mapping.
- **Not in scope:** moving the deprecated `appScreenshotSets` / `appPreviewSets` sync to the asset library (backlog; Apple gives no end date), and in-app event media (B-003).

## Edge Cases & Risks
| Case | Impact | Mitigation | Source |
|------|--------|------------|--------|
| The target is live (Ready for Distribution) | Apple refuses the placement | Check editability first and name the editable alternatives (next version, CPP, treatment) | Apple migration guide |
| Default-page art while a PPO test runs | Changes the test's base arm | The `{version: id}` target warns when an experiment is running on the app, and needs `confirm_running_test=true` | founder, 2026-10-18 |
| Ref data adds a new placement type | Hard-coded lists go stale | Types come only from ref data; an unknown filename stem is refused, with the known types listed | Apple: "new values without an API version change" |
| A PNG with alpha | Apple rejects it | The probe refuses it before upload | Apple creative specs |
| The same art in 7 locales | 7 uploads | Upload once per checksum, then place per localization | asset library model |
| The library caps assets per placement group | A placement fails partway | Read the limits from ref data, and refuse before any upload when they would be exceeded | API: per-group limit |

## Cross-Repo Interfaces
| Direction | Contract | Pinned version | Owner repo/spec | Fallback until available |
|-----------|----------|----------------|-----------------|--------------------------|
| consumes | Creative export layout `<locale>/creative/{header,search,universal}.png`: opaque, at canvas size | v1 | ox-screenshot-blessing 008 | Any directory in this layout (hand-made files work) |
| consumes | App Store Connect API asset library | 4.5.1 | Apple | — |
| publishes | MCP tools `creative_assets_*` | v1 (this spec) | aso-light 018 | — |

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | `asset_library.py`: ref data (cached), library lookup, image/video upload via the generalised `upload_asset`, place/list/delete | dev | — | todo | backend/app/services/asc/asset_library.py, backend/app/services/asc/asset_sync.py |
| T2 | Probe creative files: image size, ratio, alpha; video ratio, duration, fps | dev | — | todo | backend/app/services/asc/asset_library.py |
| T3 | `creative_assets_sync`: scan `<dir>/<locale>/creative/`, editability check, running-test guard, dry-run plan, idempotent apply | dev | T1, T2 | todo | backend/app/services/asc/asset_library.py |
| T4 | MCP tools `creative_assets_refdata/list/sync/delete` | dev | T3 | todo | backend/app/mcp/tools/creative.py, backend/app/mcp/server registration |
| T5 | Tests: probe refusals, dry-run plan, idempotent re-run, non-editable refusal, running-test guard (mocked ASC) | dev | T3 | todo | backend/tests/ |
| T6 | Contract test pinning blessing's v1 export layout (fixture tree); `docs/PINS.md` row for blessing 008 v1 and the ASC API 4.5.1 | dev | T3 | todo | backend/tests/contract/, docs/PINS.md |
| T7 | Docs: `docs/007-mcp-integration.md` tool table, INDEX, backlog row for the deprecated set resources | dev | T4 | todo | docs/ |

## Acceptance Criteria
- [ ] `creative_assets_sync` on Refresher's organic CPP plans exactly one header and one search placement per localization, and uploads each distinct file once.
- [ ] A live version as the target is refused before any upload, and the refusal names the editable alternatives.
- [ ] A PNG with alpha, or one at a wrong size, is refused with the reason.
- [ ] A re-run after apply plans no changes.
- [ ] All tests passing.
- [ ] Code review clean.
