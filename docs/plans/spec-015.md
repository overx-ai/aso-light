---
item: docs/specs/015-cpp-screenshots-sync.md
kind: spec
created: 2026-09-29
lane: go
---

## Approach
013's set/asset helpers, `plan_sync` and `apply_sync_step` move from `ASCVersionScreenshotService` up to
a base `LocalizationScreenshotService`, parameterised only by the localization type and set relationship.
The main listing and a new `CPPScreenshotService` are its two sources. Each source contributes three
things: its editable version, its `locale -> localization id` map, and, for a CPP, a localization created
on demand. The tool-level flow (root check, scan, plan, apply, read-back) becomes one
`run_screenshot_sync(bind=...)`. Each tool passes a `bind` that returns a `SyncTarget`, so
`screenshots_sync` and `cpp_screenshots_sync` share every line past target resolution.

A locale directory the CPP lacks is valid when it is one of the app's own locales, taken from the newest
App Store version's localizations. It is planned as `create_localization`, and the localization is
created lazily at the first verified upload, like a new set. The rejected alternative was a hardcoded ASC
locale table. It would drift from Apple and would accept a locale the app does not ship.

## Sequence
1. `get_editable_version` + `_EDITABLE_VERSION_STATES` (T2). Sync, delete, ensure and upload all rely on it.
2. The `NotEditableError` base, so both tool modules translate one type.
3. The base service + `SyncTarget`, and the `create_localization` action in `SyncStep` / `scan_export_dir` (T3).
4. `run_screenshot_sync` and a shared delete helper in `tools/screenshots.py`. Then the CPP tools,
   the consent entry and the upload guard (T4).
5. Docs (T5).

## Files
| File | Change |
|---|---|
| `backend/app/services/asc/screenshots.py` | `NotEditableError`, `LocalizationScreenshotService`, `SyncTarget`, create-localization planning, `app_locales` |
| `backend/app/services/asc/cpp.py` | editable states, `get_editable_version` (raises, no fallback), `CPPVersionNotEditableError`, `CPPScreenshotService`, `assert_localization_editable` |
| `backend/app/mcp/tools/screenshots.py` | `run_screenshot_sync`, shared delete helper |
| `backend/app/mcp/tools/cpp.py` | `cpp_screenshots_sync`, `cpp_screenshots_delete`, guards on ensure/upload |
| `backend/app/mcp/consent.py` | `cpp_screenshots_delete` in `DESTRUCTIVE` |
| `backend/app/schemas/screenshots.py` | `create_localization` action |
| `backend/app/api/v1/cpp.py` | from-upload maps the not-editable error to 409 (it used to catch `RuntimeError`) |
| `backend/tests/test_cpp.py`, `backend/tests/test_mcp_screenshots.py` | tests |
| `docs/007-mcp-integration.md`, `docs/000-changelog.md` | tool reference |

## Tests first
- `get_editable_version` returns the PREPARE_FOR_SUBMISSION version. IN_REVIEW / WAITING_FOR_REVIEW only
  is refused with those states named, and no version at all is refused. It never falls back to `versions[0]`.
- Dry run: an existing CPP locale that matches is `skip`, a stale one is `replace`, and an app locale the
  CPP lacks is `create_localization`. No write.
- Apply: the stale set holds exactly the directory's files in order, and extras are deleted. The missing
  localization is created and filled. The read-back counts are reported.
- A rerun is all `skip` with zero writes.
- A Watch set on the same CPP localization is never named in any call.
- IN_REVIEW is refused by `cpp_ensure_localization`, `cpp_upload_screenshot`, `cpp_screenshots_sync` and
  `cpp_screenshots_delete`, naming the state, with no write.
- `cpp_screenshots_delete` is in `DESTRUCTIVE`, and the gate refuses it without a token. It deletes by
  position through the shared helper.
- 013's directory rules against the CPP source: `variants/`, dot-dirs and top-level files are untouched.
  An unknown size is an error, and `nl/` (not an app locale) is an error. A dir outside the roots is
  refused before any ASC call.

## Risks
- `cpp_upload_screenshot` has only a localization id, so its guard reads
  `GET /appCustomProductPageLocalizations/{id}?include=appCustomProductPageVersion`. This has not been
  checked against live ASC. If Apple refuses the include, uploads fail loudly rather than landing on a
  reviewed version. The fallback is to require `cpp_id` on that tool.
- Apple may accept CPP locales outside the app's own set. The allowlist then refuses a valid directory,
  which is an error, never a silent skip. The fix is widening `creatable`.

## Deviations
- `backend/app/api/v1/cpp.py` and `backend/app/mcp/consent.py` are outside the spec's Files column.
  The REST from-upload route caught `RuntimeError` for the old "no version" `None`, and the no-fallback
  resolution now raises `CPPVersionNotEditableError`. It maps to 409 instead of an unhandled 500.
  `consent.py` is where req 7's gate lives.
- `cpp_screenshots_delete` also takes `prune_empty_set` (default false), for parity with
  `screenshots_delete`, since both call one shared helper.
- `test_sync_replaces_a_failed_asset_even_when_its_checksum_matches` (013) now gives its bare `SyncStep`
  a `localization_id`. A step without one now means "create the localization".
- `SyncTarget` and `scan_export_dir(root, target, ...)` replace the scan's `localizations` /
  `version_label` arguments, and the unknown-locale message reads "not a locale on {label}". The
  main listing passes "App Store version 1.5.0".
- `/code` review:
  - `create_cpp_with_screenshots` now resolves the version and localization inside the cleanup `try`, so a page it cannot populate is deleted, not orphaned.
  - `apply_sync` hands a created localization to the locale's other display types instead of listing it again.
  - A localization whose version is not returned is refused as "unknown".
- `/docs`: docs/007, docs/000-changelog and docs/013 are updated. `docs/INDEX.md` and `CLAUDE.md` ("187 tools", now 190) are **not**, because the main copy holds uncommitted WIP in both. They need a spec-015 row and the tool count once that WIP lands.
