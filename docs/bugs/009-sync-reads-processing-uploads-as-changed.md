---
id: 009
title: "A sync plans 'replace' for screenshots Apple is still processing, and a re-apply deletes good uploads"
status: fixed
severity: high
created: 2026-09-30
updated: 2026-09-30
source: manual
repo: aso-light
release: 1.6.0
files: backend/app/services/asc/screenshots.py, backend/app/schemas/screenshots.py, backend/app/mcp/tools/screenshots.py, backend/tests/test_mcp_screenshots.py
---

# BUG 009 - A sync reads a still-processing upload as changed

> **TL;DR** — For a while after upload, Apple reports a screenshot with no source checksum. The
> planner compares checksums, so it counts that slot as changed and plans a `replace`. Re-applying on
> that plan deletes a correct upload and sends it again, which restarts Apple's processing. The fix: a
> slot that is still processing and holds the same file name and byte size counts as unchanged, and the
> row reports how many slots are still processing.

## Symptom

- Mushtra 1.6.0, the No gamification CPP, iPad 13" (2026-09-29/30). Every locale had just been applied
  7/7.
- A dry run minutes later showed `replace` on 37 of 39 locales, with 1–6 slots "changed" each, while
  `existing` was 7 everywhere.
- The same dry run hours later showed `skip` on ar-SA, de-DE, hi and zh-Hans, which had read 5, 5, 6 and
  6 changed slots before. Nothing was uploaded in between.
- `cpp_list_screenshots` for those slots showed `source_url: null` while processing.

**Reproduction:** apply a locale, then dry-run it again within minutes. The processed slots skip, and
the rest plan `replace`.

**Expected → actual:** a slot we just uploaded, same name, still processing, is `skip` (reported as
processing). Actual: it is `replace`. Worse, an `apply` on that plan deletes it and uploads it again.

## Root cause

- `_slot_matches` (`backend/app/services/asc/screenshots.py`) needs `checksum == md5` and a state that
  is not `FAILED`. It is the one slot rule: the plan (`SyncStep._changed`, so `uploads`, `deletes` and
  `action`) and the apply (`apply_sync_step`, which keeps a matching slot in place and deletes and
  re-uploads any other) both read it.
- The checksum is sent on commit (`upload_screenshot`), but Apple withholds `sourceFileChecksum` for a while
  after the commit. Confirmed live on 2026-09-30, on a PPO treatment's en-US iPhone set right after an
  apply: `08.png` read `COMPLETE` with no checksum, then `COMPLETE 471a3938…` 20 seconds later. On the
  CPPs, some slots stayed that way for hours.
- The state is no signal: it already reads `COMPLETE`.

## Fix

- A slot is unchanged when its checksum matches as before, **or** when all four of these hold
  (`_slot_processing`):
  - its checksum is `None`;
  - it has a state, and that state is neither `AWAITING_UPLOAD` (a reservation never committed) nor
    `FAILED`;
  - its file name equals the export file's;
  - its `fileSize` equals the export file's byte count. The studio re-exports the same names
    (`01.png`…) after every edit, so a name alone would skip an edited slide for as long as Apple
    withholds the checksum — hours, on some CPP slots. `fileSize` is sent on the reservation
    (`upload_screenshot`) and read back by `list_set_screenshots`; the export's size comes from the
    same single read that computes its MD5 (`ExportFile.size`).
- Because `_slot_matches` also governs `apply_sync_step`, a processing slot is kept in place on apply:
  no delete, no re-upload.
- `SyncStep.processing` counts those slots, and the row carries `processing` (`ScreenshotSyncRow`,
  `_sync_row`), so a caller sees "wait", not "done".
- The first draft of this fix keyed on `UPLOAD_COMPLETE` and was wrong. The live read corrected it, and
  the `COMPLETE` case is a test.

## Regression test

In `backend/tests/test_mcp_screenshots.py`:

- `test_sync_counts_a_processing_upload_of_the_same_file_as_unchanged[UPLOAD_COMPLETE|COMPLETE]`: checksum `None`, same name and size → `skip`, `processing == 1`, no writes on apply. Both were red first: the first on the missing field, `COMPLETE` on the first draft's rule.
- `test_sync_replaces_a_processing_slot_holding_another_file`: same state, different name → `replace`.
- `test_sync_replaces_an_upload_that_was_never_committed`: `AWAITING_UPLOAD`, same name → `replace`.
- `test_sync_replaces_a_processing_slot_of_the_same_name_but_another_size`: same name, size off by one → `replace`.
- `test_sync_replaces_a_checksumless_slot_with_no_state`: no state at all → `replace`.
- `test_sync_skips_its_own_upload_while_apple_withholds_the_checksum`: an applied locale whose checksums are then withheld plans `skip` with `processing == 2`. It goes through the fake's reservation (which stores `fileSize`) and its listing (which returns `fileSize` only when the fieldset asks for it), so dropping `fileSize` from `list_set_screenshots` turns it red.

## Residual limit

- A re-export whose bytes changed but whose byte count did not, landing in a slot whose checksum Apple
  still withholds, is taken as the file and skipped. The row reports it as `processing`, and once Apple
  fills the checksum a rerun sees the mismatch and replaces it. So wait until a dry run shows
  `processing == 0` before trusting a `skip`.
- Only `list_set_screenshots` (the sync's read) asks for `fileSize`; the other delivery-state reads carry
  `file_size: None`. A slot Apple returns without `fileSize` plans `replace`, the pre-fix behaviour: the
  rule fails safe, never silent. Apple does fill `fileSize` while it withholds the checksum. This was
  confirmed live on 2026-09-30, mid-upload of a PPO treatment: `06.png COMPLETE None 1505211`.

## Notes

- Found during the Mushtra CPP iPad uploads, alongside [bug 007](007-asc-client-does-not-retry-5xx.md):
  after a 5xx the natural move is to re-run the sync, and without this fix a re-run damages what already
  landed.
