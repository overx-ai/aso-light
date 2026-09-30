---
id: 009
title: "A sync plans 'replace' for screenshots Apple is still processing, and a re-apply deletes good uploads"
status: open
severity: high
created: 2026-09-30
updated: 2026-09-30
source: manual
repo: aso-light
release: 1.6.0
files: backend/app/services/asc/screenshots.py, backend/tests/test_mcp_screenshots.py
---

# BUG 009 - A sync reads a still-processing upload as changed

> **TL;DR** — For a while after upload, Apple reports a screenshot with no source checksum. The
> planner compares checksums, so it counts that slot as changed and plans a `replace`. Re-applying on
> that plan deletes a correct upload and sends it again, which restarts Apple's processing. The fix: a
> slot that is still processing and holds the same file name counts as unchanged, and the row reports
> how many slots are still processing.

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
  is not `FAILED`.
- The checksum is sent on commit (`upload_screenshot`). Yet the same slots read as changed while they
  processed and as matching afterwards, so Apple evidently withholds `sourceFileChecksum` until the
  asset is processed.
- This is inferred from the before/after dry runs. It gets confirmed live by reading one slot's raw
  attributes right after an apply.

## Fix

- A slot is unchanged when its checksum matches as before, **or** when all three of these hold:
  - its checksum is `None`;
  - its delivery state is `UPLOAD_COMPLETE` (committed, still processing);
  - its file name equals the export file's.
- `AWAITING_UPLOAD` is a reservation that was never committed (a failed PUT or commit), and `FAILED`
  failed. Both still read as changed, so they are replaced.
- `SyncStep` counts those slots as `processing`, and the row carries it, so a caller sees "wait", not
  "done".

## Regression test

In `backend/tests/test_mcp_screenshots.py`:

- `test_sync_counts_a_processing_upload_of_the_same_file_as_unchanged`: `UPLOAD_COMPLETE`, checksum
  `None`, same name → `skip`, `processing == 1`, no writes on apply.
- `test_sync_replaces_a_processing_slot_holding_another_file`: same state, different name → `replace`.
- `test_sync_replaces_an_upload_that_was_never_committed`: `AWAITING_UPLOAD`, same name → `replace`.

## Notes

- Found during the Mushtra CPP iPad uploads, alongside [bug 007](007-asc-client-does-not-retry-5xx.md):
  after a 5xx the natural move is to re-run the sync, and without this fix a re-run damages what already
  landed.
