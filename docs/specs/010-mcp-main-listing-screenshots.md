---
id: 010
title: "MCP: main product-page screenshots — list, upload, delete"
status: done
created: 2026-08-17
updated: 2026-08-24
tasks: []
---

# 010 - Main Product-Page Screenshots over MCP

## Problem

`backend/app/services/asc/screenshots.py` is already **parent-agnostic**: `upload_screenshot_to_localization`
takes a `localization_type` / `localization_id` pair, and its own docstring notes that the
`appScreenshotSets` → `appScreenshots` model is identical whichever localization owns it. Custom product
pages (`cpp_upload_screenshot`) and experiment treatments (`experiment_upload_treatment_screenshot`) both use
it today.

Nothing exposes it for the app's **main** product page (`appStoreVersionLocalizations`). `mcp/tools/screenshots.py`
contains exactly one tool, `screenshots_compare` (visual diff). So an MCP client that generates localized
screenshots — e.g. a 40-locale run — must upload the main set through `fastlane deliver` or the ASC web UI.

The sharper problem is **counting, not uploading**. ASC's post-upload polling is flaky and retries HTTP 500s;
an interrupted bulk run leaves *some locales silently short*. Apple then rejects the version because a
configured device class is incomplete — and that error surfaces **only at submit time**, after the run looks
finished. Without an API-side count, the only repair is opening 40 locales in the ASC UI by hand.

## Scope

In:
- `screenshots_list` — for the editable version: per locale × display type, the screenshot count and the
  existing assets. This is the resume/repair primitive and the reason the spec exists.
- `screenshots_upload` — app + locale + display type + image → resolves the editable version's
  `appStoreVersionLocalization` and reuses `find_or_create_screenshot_set` and
  `upload_screenshot_to_localization` **unchanged**. Reserve → upload → commit. Idempotent per
  (locale, display type, position).
- `screenshots_delete` — remove a screenshot (and optionally an empty set) so a wrong set can be replaced.
  **No delete exists anywhere in the service layer today**, so this is the one genuinely new service function.
- Per-display-type **completeness** in the list response: enough to answer "is this version submittable?"
  before Apple answers it for you.

Out (deferred):
- App version creation and submit-for-review.
- Reordering beyond delete + re-upload.
- App preview videos (`appPreviewSets`) — same shape, separate spec if wanted.
- Any change to CPP or experiment screenshot behaviour.

## ASC API surface used

Already wrapped by `services/asc/screenshots.py`; the new tools only add the main-listing parent and delete:

- `GET /v1/appStoreVersionLocalizations/{id}/appScreenshotSets?include=appScreenshots`
- `POST /v1/appScreenshotSets` — parent `appStoreVersionLocalization`
- `POST /v1/appScreenshots` (reserve) → `PUT` upload operations → `PATCH /v1/appScreenshots/{id}`
  with `uploaded: true` (commit)
- `DELETE /v1/appScreenshots/{id}`, `DELETE /v1/appScreenshotSets/{id}`

## Requirements

1. **Resolve the editable version.** Live/locked versions have no editable screenshot sets — return a clear
   error naming the version state rather than a raw 409. No version creation (out of scope).
2. **A 2xx is not verification.** After commit, read the asset back and report `state`; report only what the
   read-back confirms. An asset stuck in a non-complete state is a failure, not a success.
3. **Idempotent uploads.** Re-uploading the same (locale, display type, position) replaces rather than
   duplicating — a resumed bulk run must not double a locale.
4. **Reuse, don't fork.** `find_or_create_screenshot_set` and `upload_screenshot_to_localization` are shared
   with CPP and experiments; extend them only in backward-compatible ways. Their existing callers must be
   provably unaffected.
5. Follow existing MCP tool conventions in `mcp/tools/` (naming, schema placement, error shape).

## Tasks

| # | Task | Files |
|---|---|---|
| 1 | `delete_screenshot` / `delete_screenshot_set` service functions | `backend/app/services/asc/screenshots.py` |
| 2 | `screenshots_list`, `screenshots_upload`, `screenshots_delete` MCP tools | `backend/app/mcp/tools/screenshots.py` |
| 3 | Request/response schemas | `backend/app/schemas/screenshots.py` |
| 4 | Tests: per-locale count/completeness, idempotent re-upload, and a contract test proving CPP + experiment paths are unchanged | `backend/tests/` |

## Acceptance Criteria

- `screenshots_list` returns per-locale × display-type counts for the editable version, and flags any display
  type that is configured but incomplete.
- Uploading the same position twice leaves exactly one screenshot.
- Deleting the last screenshot in a set leaves no orphan set.
- Existing `cpp_upload_screenshot` and `experiment_upload_treatment_screenshot` tests pass untouched.
- Attempting to upload against a live/locked version fails with a message naming the version state.

## Cross-Repo Interfaces

Soft reference only — **no hard dependency in either direction**:

- Consumer: the `vibe-aso` skill (`~/.claude/skills/vibe-aso`), phase 3. It ships today using
  `fastlane deliver` for bulk upload and **manual UI verification** of per-locale counts; its
  `reference/screenshots.md` names that as the interim path. When these tools exist it uses
  `screenshots_list` to find short locales and `screenshots_upload` to repair them.
- Nothing in aso-light depends on that skill. This spec stands alone.

## Implementation Notes (post-merge)

- **Where the code lives.** The main-listing service is `ASCVersionScreenshotService` in
  `backend/app/services/asc/screenshots.py` — same module as the parent-agnostic helpers, whose
  docstring now names both halves. It reuses `ASCMetadataService` for the version/localization
  reads rather than forking a second copy of that walk.
- **Completeness is relative, not absolute.** `expected` per display type defaults to the
  *highest count any locale reached* (min 1), so the locales that finished a bulk run define the
  target and the interrupted ones surface as `gaps`. `expected_count` pins it explicitly. An
  Apple-`FAILED` asset never counts toward `count` — it occupies a slot without being a shipped
  screenshot, which is exactly how a version looks complete and still gets rejected.
- **`include_assets` defaults to `False`** (deviation from "the existing assets" in Scope). A
  40-locale × 3-device report is a *counting* payload; the per-asset ids / CDN urls / delivery
  states multiply its size several-fold and are only needed when repairing. Pass
  `include_assets=True` to get them.
- **Requirement 2 is reported, not raised.** After commit the asset is re-read (up to 3 times);
  `FAILED` or any `assetDeliveryState.errors` raises, but an asset still in `UPLOAD_COMPLETE`
  returns `verified=False` with a `warning` naming the state. Raising there would make a resumed
  run re-upload an asset Apple is merely still processing — the opposite of idempotent.
- **Replace deletes before uploading.** Apple caps a set at 10 assets, so upload-then-delete
  would fail on a full set. The new asset is then moved into the vacated slot via
  `PATCH /appScreenshotSets/{id}/relationships/appScreenshots`.
- **Shared-helper compatibility** is provable: `fetch_screenshot_sets` gained an opt-in
  `include_delivery_state` flag only — the default request and shaped dict are byte-identical for
  CPP and PPO, pinned by contract tests in `backend/tests/test_mcp_screenshots.py`.
