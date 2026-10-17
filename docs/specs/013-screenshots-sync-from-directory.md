---
id: 013
title: "MCP: sync the main listing's screenshots from a local export directory"
status: done
created: 2026-09-23
updated: 2026-09-29
repo: aso-light
release: 1.6.0
depends_on: []
conflicts_with: []
tasks: []
---

# 013 - Screenshots Sync from a Directory

> **TL;DR** — `screenshots_sync {app_id, dir}` makes a studio export (`<dir>/<locale>/NN.png`) the
> editable version's screenshots in one call: display type from pixel size, only the types present
> are replaced, Watch and every other set untouched, unchanged files skipped by checksum. It replaces
> the per-app fastlane staging and ~160 base64 `screenshots_upload` calls.

**Prerequisites**: [010 - Main product-page screenshots](010-mcp-main-listing-screenshots.md)

## Problem

The screenshot studio (`4TOOLS/ox-screenshot-blessing`) exports `versions/<v>/out/<locale>/NN.png`, one
directory per device family. Getting that into App Store Connect today means either:

- `screenshots_upload` per file, base64 through the agent's context — 8 slides × 10 locales × 2 families
  is 160 calls and ~300 MB of tokens-as-bytes; or
- the app repo's own `fastlane deliver`, which cannot replace one display type: its delete filters on
  locale, so it wipes the Watch set (review-blocking for an app with a watchOS target). JustBreathe
  works around it with `overwrite_screenshots: false`, a Python stager tied to one manifest format and
  hardcoded sizes, and a `--dedupe` pass afterwards. Every app would need its own copy.

asolight already owns the one correct primitive (010: per-set replace, read-back, per-locale counts).
It runs on the host (panel app `aso-light/backend`, uvicorn), so it can read the export in place.

## Scope

In:
- `screenshots_sync` MCP tool (`backend/app/mcp/tools/screenshots.py`), same service layer as 010.
- Display type inferred from image size; a size table in one place.
- Dry run by default.

Out:
- Creating the App Store version (still manual, as in 010).
- CPP / PPO treatment sync — same shape, a follow-up once this one is proven.
- App preview videos.

## Tool

```
screenshots_sync {
  app_id: int,
  dir: str,                     # absolute; <dir>/<locale>/*.png|jpg
  locales?: [str],              # default: every locale dir present
  display_types?: [str],        # default: every type inferred from the files
  apply: bool = false           # false → plan only, nothing written
}
```

Returns, per locale × display type: `action` (`skip` | `replace` | `upload` | `error`), file count,
existing count, and after `apply` the read-back from the 010 inventory (`count`, `gaps`). Plus
`untouched`: locales on the version with no directory, and display types on the version not in `dir`.

## Requirements

1. **Only the display types present are touched.** A set whose type no file maps to is never read for
   deletion. This is the whole reason the tool exists — never regress to deliver's per-locale wipe.
2. **Display type from pixels, one table.** e.g. 1320×2868 / 1290×2796 → `APP_IPHONE_67`,
   2064×2752 / 2048×2732 → `APP_IPAD_PRO_3GEN_129`, the Watch sizes → `APP_WATCH_*`. Portrait and
   landscape both. An unknown size, or two types in one locale dir that the caller did not split, is an
   `error` row naming the file and its size — never a guess.
3. **Order is filename sort** (`01.png` … `10.png`). More than 10 files for one type → error before any
   write.
4. **An unknown locale directory is an error, not a skip.** A directory that is not an ASC locale code, or
   a locale not on the editable version, is reported. (`deliver` skips it silently, which once shipped a
   "successful" push of nothing.)
   Two exceptions, listed under `untouched` and never an error: dot-directories (the studio's
   `.history/`) and `variants/` — the studio's Custom Product Page exports, synced by spec 015.
   Top-level files (`findings.json`, `qa.json`) are ignored.
5. **Idempotent and resumable.** A slot whose asset's `sourceFileChecksum` equals the file's MD5 is
   `skip`. Rerunning after an interruption uploads only what is missing or changed.
6. **Replace a type as a unit.** When any file of a type changed, the set ends with exactly the
   directory's files in the directory's order — via 010's replace-into-slot, so a full 10-asset set never
   overflows. Extra trailing assets beyond the directory's count are deleted.
7. **Path is a trust boundary.** `dir` must resolve (after `realpath`) under an allowlisted root from
   settings (`SCREENSHOT_SYNC_ROOTS`, default the user's workspace root). Symlinks leaving it, non-image
   files and files over ASC's size limit are refused.
8. **A 2xx is not verification** (010 req. 2): the response's final state is the read-back inventory,
   not the upload responses.
9. **Dry run is the default.** `apply: false` makes no ASC write and no delete.

## Tasks

| # | Task | Files |
|---|---|---|
| 1 | Size → display type table and inference (with portrait/landscape) | `backend/app/services/asc/screenshots.py` |
| 2 | Directory scan: locale validation, root allowlist, per-type grouping, MD5 | `backend/app/services/asc/screenshots.py` |
| 3 | Plan (diff against inventory) and apply (reuse 010 replace/upload/delete) | same |
| 4 | `screenshots_sync` MCP tool + schema | `backend/app/mcp/tools/screenshots.py`, `backend/app/schemas/screenshots.py` |
| 5 | Tests: Watch set untouched while iPhone replaced; unknown size/locale → error; rerun is all `skip`; symlink out of root refused; dry run writes nothing | `backend/tests/test_mcp_screenshots.py` |

## Acceptance Criteria

- On a version holding iPhone, iPad 12.9", iPad 11" and Watch sets, syncing a directory of iPhone files
  replaces the iPhone set and leaves the other three byte-identical (same asset ids).
- A second `apply` on the same directory reports every row `skip` and makes no write.
- `apply: false` returns the same plan `apply: true` then executes.
- An `nl/` directory, a 1000×1000 PNG and a symlink to `/etc` each produce an `error` row and no write.

## Cross-Repo Interfaces

- Producer: ox-screenshot-blessing `export` → `projects/<app>/versions/<v>/out/<locale>/NN.png`, one
  project (so one `dir`) per device family, locale directories already ASC codes. The studio never uploads.
- Consumer: the `vibe-aso` skill phase 3 and any agent shipping a release. Once this exists, the
  JustBreathe `stage_appstore_screenshots.py` + `upload_screenshots` lane are no longer the screenshot
  path for that app.
