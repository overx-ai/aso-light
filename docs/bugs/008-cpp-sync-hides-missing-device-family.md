---
id: 008
title: "cpp_screenshots_sync reports clean for a page that lacks a device family the app ships"
status: fixed
severity: high
created: 2026-09-30
updated: 2026-09-30
source: manual
repo: aso-light
release: 1.6.0
files: backend/app/mcp/tools/screenshots.py, backend/app/mcp/tools/cpp.py, backend/app/services/asc/screenshots.py, backend/app/schemas/screenshots.py, backend/tests/test_mcp_screenshots.py
---

# BUG 008 - A CPP sync says nothing about a missing device family

> **TL;DR** — `cpp_screenshots_sync` touches only the display types present in the directory,
> so a page synced with iPhone shots plans and applies clean while lacking the iPad set. App
> Store Connect then refuses to submit it. The result now carries `missing_families`: per
> locale, the display types the app's main listing holds that the page would still lack.

## Symptom

- Mushtra 1.6.0: three CPPs were synced from a studio export of 39 locales × 8 iPhone shots.
  Every dry run and apply was clean, the read-back showed 8 of 8 per locale.
- Submitting a page for review failed in App Store Connect with «Не удается добавить для
  проверки — Необходимо загрузить снимок экрана для iPad Pro с 13-дюймовым дисплеем»
  (cannot add for review: a screenshot for the iPad Pro 13-inch display is required).
- The main listing ships iPad (7) and iPhone (8), so a page must carry both.

**Expected → actual:** the sync result shows that the page is short of a family the app ships.
Actual: nothing, since a display type absent from `dir` is never read.

## Root cause

By design (spec 013/015) a sync reads only the display types in the export, so another type is
"untouched" and never compared with anything. Nothing relates the page to the app's own
families, and the read-back `inventory` counts only the types the sync touched.

## Fix

- `missing_families(target, steps)` in `services/asc/screenshots.py`, the one helper, for both
  dry run and apply: per locale, `target.reference` (the display types the app's editable
  main version holds, read by `ASCVersionScreenshotService.main_families`) minus what the
  page holds minus what the plan adds. Locales are the page's localizations plus those the
  plan creates.
- `SyncTarget.reference` is set by the CPP binder only, so `screenshots_sync` returns an empty
  dict. It is also empty when the app has no editable main version.
- `ScreenshotSyncResult.missing_families: dict[str, list[str]]`. Informational: an apply never
  refuses on it, since a partial sync in stages is legitimate. The tool docstring says so.

## Regression test

`backend/tests/test_mcp_screenshots.py`:

- an iPhone-only page under an iPad-shipping main lists the iPad type per locale, in the dry
  run and after the apply (red before: no field);
- after the iPad export is applied too, the list is empty;
- a non-editable main version gives an empty list;
- `screenshots_sync` returns an empty list.

## Data fix (Mushtra 1.6.0)

The 7 framed iPad shots per locale are uploaded to each CPP with `display_types=
["APP_IPAD_PRO_3GEN_129"]`, in two batches of about 20 locales to stay under the MCP client's
10-minute call limit.

## Known limits

- An existing but empty set counts as holding its family. `screenshot_set_ids` lists display types
  without counting shots, so an emptied iPad set reads as present. Deleting through `cpp_screenshots_delete`
  without `prune_empty_set` can leave one. Counting would cost a read per set.
- `missing_families` reports every page locale, whatever the `locales` filter: submission is judged per
  page, not per sync call.
- Red before green: the four tests failed on main's code (`AttributeError: missing_families`), then passed
  on the branch.
