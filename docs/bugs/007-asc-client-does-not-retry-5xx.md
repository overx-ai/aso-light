---
id: 007
title: "The ASC client retries 429 only, so one Apple 5xx or network blip fails a whole screenshot sync"
status: fixed
severity: medium
created: 2026-09-29
updated: 2026-09-30
release: 1.6.0
source: manual
repo: aso-light
files: backend/app/services/asc/client.py, backend/app/services/asc/errors.py, backend/app/services/asc/screenshots.py, backend/app/mcp/tools/screenshots.py, backend/app/mcp/tools/cpp.py, backend/app/schemas/screenshots.py, backend/tests/test_asc_client.py, backend/tests/test_mcp_screenshots.py
---

# BUG 007 - One Apple 5xx fails a sync row; only 429 is retried

> **TL;DR** — `ASCClient._request` retries 429 (and one 401) and raises `ASCAPIError` on every
> other 4xx/5xx, so a transient Apple 500 or 503 aborts the row it lands on. During the Mushtra
> 1.6.0 uploads a few CPP rows failed this way and were only recovered by re-running the whole
> batch, which the MD5 skip made safe. The client should retry 5xx itself.

## Symptom

- `cpp_screenshots_sync` / `screenshots_sync` apply reports a row error carrying an Apple 500
  (sometimes 503) on a delete or an upload, in the middle of a healthy 312-upload batch.
- The row is left half done: the old set may already be deleted and the new one not yet complete.
- A second identical call fixes it, because unchanged slots are skipped by MD5 and the failed row
  is re-planned. Nothing in the tool result says a retry is expected to work.

**Reproduction:** none on demand, since Apple's 5xx are transient. A test can force one:
mock the transport to answer 500 once and 200 next, call any `_get` / `_post`, expect the value
of the second response.

**Expected → actual:** a 5xx is retried a bounded number of times with backoff, and only a
persistent one surfaces. Actual: the first 5xx raises `ASCAPIError` immediately.

## Root cause

`backend/app/services/asc/client.py` `_request` (around line 156): the retry loop handles 401
(attempt 0 only) and 429, then `if response.status_code >= 400: raise ASCAPIError`. The
paginated GET loop (around line 296) has the same shape. 5xx is not in either.

## Fix

The fix has two layers.

**Client** (`client.py`): one `_send` retry loop serves `_request`, pagination, the upload `PUT` and the
download `GET`.
- **429:** retried as before, with a global backoff. The upload `PUT` now gets this too.
  - A `Retry-After` that is an HTTP date, not seconds, falls back to the exponential delay.
- **500/502/503/504, read timeouts and dropped connections:** retried with exponential backoff, on
  `GET`, `PUT`, `PATCH` and `DELETE` only.
  - A `POST` that timed out or answered 5xx may still have created its resource, so it is never retried
    blind.
- **A connect failure:** retried for any method, since nothing left the machine.
- **An unretried or persistent network failure:** raises `ASCNetworkError`, an `ASCAPIError` whose
  message says "network error" and names the cause.
  - Its status is 504: REST handlers pass `status_code` straight to `HTTPException`, so it must be a
    valid HTTP status.
- **401:** the first one in a call refreshes the token, whichever attempt it lands on, for `_request`
  and pagination. The pre-signed upload and download URLs carry no token.
- **A final 4xx/5xx:** `ASCAPIError` (`ASCRateLimitError` for 429), the same for all four paths. A body
  that is not a JSON object (Apple's edge answers HTML) becomes the error's detail instead of a
  decode error.

**Sync** (`LocalizationScreenshotService._apply_or_record`):
- A row that raises `ASCAPIError` is re-planned from its live set and applied once more.
  - The re-plan sweeps what the first attempt left half done: a reserved asset whose `PUT` or commit
    failed stays `AWAITING_UPLOAD` and reads as changed, so it is replaced or deleted.
  - This is what makes the `POST`-not-retried rule safe.
- A second failure is recorded on the step. The row carries `error: "apply failed twice: …"`, and the
  apply finishes every other row instead of aborting the call.
- The read-back inventory shows the gap, and a re-run repairs it. A locale whose localization could
  not be created is left out of the read-back rather than read as `None`.
- The `screenshots_sync` and `cpp_screenshots_sync` descriptions say so, so an agent reads `error`
  on an applied row.
- `ExportChangedError` (a file changed between plan and apply) still aborts: that is the caller's data,
  not Apple.

## Regression tests

Red first, then green; both recorded here.

- **`backend/tests/test_asc_client.py`: 26 tests.**
  - Red on the old client: 6 tests for 5xx (`GET`, `PATCH`, `DELETE`, persistent 503, pagination,
    upload `PUT`).
  - Red on the old client: 9 tests for the rest (non-JSON 5xx, `GET` read timeout, `POST` connect error,
    `POST` read timeout not retried, persistent network error, pagination read timeout, upload `PUT` on
    429, read timeout and connect error).
  - Green before and after, by design, as regression guards: `POST` 5xx not retried, and 429 still
    retried.
  - Red on the first version of the fix: 7 tests for the review (a network error's status, upload and
    download 429 raising `ASCRateLimitError`, a 401 late in a retry chain, a 401 on a later page, a
    `Retry-After` date, a JSON error body that is not an object).
  - Guards: a second 401 raises after one refresh, and a 401 on the last attempt raises it.
- **`backend/tests/test_mcp_screenshots.py`: 4 tests.**
  - `test_sync_apply_retries_a_failed_row_from_live_state_and_sweeps_the_orphan`
  - `test_sync_apply_reports_a_row_that_fails_twice_and_finishes_the_others`
  - `test_sync_rerun_after_a_failed_row_repairs_it`

  All three were red (the call died with `ToolError`) before the fix.
  - `test_cpp_sync_whose_localization_fails_twice_reads_back_only_real_localizations` was red on the
    first version of the fix: the read-back built its inventory with a `None` localization and the
    call died with a validation error.
- **Rewritten test:** `test_the_apply_guard_is_released_when_an_apply_fails` used an ASC 5xx to abort an
  apply, which no longer aborts. It now uses `ExportChangedError`, and its intent (the guard is
  released) is unchanged.

## Clone

`clone.py` retried a failed POST once on any status of 500 or above. Now that a network failure arrives as
`ASCNetworkError` (504), that retry would re-send a POST that may have landed. `_retry_post_once` (tested
in `tests/test_clone_post_retry.py`, red first on the missing function) retries only an Apple 5xx answer.

## Notes

- Related to [bug 006](006-screenshot-sync-apply-silent-past-client-timeout.md): retries make a
  slow row slower, and the progress heartbeat already covers that.
- Tagged for 1.6.0: the Mushtra PPO treatment uploads (about 1,750 shots) run on it.
