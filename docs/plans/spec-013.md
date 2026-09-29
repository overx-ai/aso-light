---
item: docs/specs/013-screenshots-sync-from-directory.md
kind: spec
created: 2026-09-29
lane: go
---

## Approach
Split the tool into a pure filesystem scan and an ASC plan/apply. The scan (size table, locale
validation, root allowlist, MD5) needs the version's locales, so it runs after the editable version
resolves, but the root check runs first and refuses before any ASC call. The plan reads only the
sets of the display types the export holds (a light `display type -> set id` lookup, then that set's
assets with `sourceFileChecksum`). Apply reuses 010's slot replace: delete a changed slot's asset,
upload its file, then one reorder, with trailing extras deleted. Any `error` row blocks the whole
apply. The rejected alternative was writing the clean rows and reporting the bad ones: a half-synced
release is exactly the "successful push of nothing" failure the spec exists to prevent.

## Sequence
1. Size table + `display_type_for_size` (task 1). Everything else keys on it.
2. Root allowlist + directory scan (task 2).
3. `screenshot_set_ids` (shared with `find_or_create_screenshot_set`), `plan_sync`, `apply_sync_step` (task 3).
4. Schemas + the `screenshots_sync` tool, read-back through `_build_inventory` (task 4).

## Files
| File | Change |
|---|---|
| `backend/app/services/asc/screenshots.py` | size table, scan, plan/apply; checksum in the set read |
| `backend/app/mcp/tools/screenshots.py` | `screenshots_sync` tool |
| `backend/app/schemas/screenshots.py` | `ScreenshotSyncRow`, `ScreenshotSyncUntouched`, `ScreenshotSyncResult` |
| `backend/app/core/config.py` | `SCREENSHOT_SYNC_ROOTS` (default `~/JACK`), shared list parser |
| `backend/tests/test_mcp_screenshots.py` | FakeASC keeps checksums; 15 sync tests |

## Tests first
- Size table: 1320x2868 and 1290x2796 map to `APP_IPHONE_67`, landscape too; 1000x1000 maps to nothing.
- Dry run returns skip / replace / upload / error rows with the right counts, and makes no write.
- The dry-run plan equals the plan `apply` executes.
- Apply leaves each set as the directory's files in order, with extras deleted; the read-back count is reported.
- A rerun is all `skip` and makes no write.
- Watch and iPad sets stay byte-identical, and their ids never appear in a call.
- An unknown size, an `nl/` directory, over 10 files, or a symlink leaving the roots each give an error row and no write.
- A `dir` outside the roots is refused before any ASC call.
- `variants/`, dot-directories and top-level files are listed under `untouched`.
- Two display types in one locale directory are an error unless `display_types` narrows it.

## Risks
- ASC might not return `sourceFileChecksum` for some assets. The fallback is safe: a missing checksum never matches, so the slot is replaced rather than wrongly skipped.
- Pixel sizes beyond the table (6.3"/6.1" iPhones, iPad mini) are errors, not guesses. Add rows when an export needs them.

## Deviations
- This plan was written down after the red tests, in the same session, rather than before them. The approach did not change.
- The consent gate (`app/mcp/consent.py`) is not touched. `screenshots_sync` sits in the default write tier (it prompts, with no token), like `screenshots_upload`. Gating it in `DESTRUCTIVE` would require a token for every dry run too.
