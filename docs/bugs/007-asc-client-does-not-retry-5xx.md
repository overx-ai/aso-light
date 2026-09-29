---
id: 007
title: "The ASC client retries 429 only, so one Apple 5xx fails a whole screenshot sync row"
status: open
severity: medium
created: 2026-09-29
updated: 2026-09-29
source: manual
repo: aso-light
files: backend/app/services/asc/client.py, backend/app/services/asc/screenshots.py, backend/tests/test_asc_client.py
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

- One predicate for "transient": 429 stays as it is; 500, 502, 503 and 504 join it, with
  exponential backoff from `_BACKOFF_BASE` inside the existing `_MAX_RETRIES` loop. Share it
  between `_request` and the pagination loop rather than pasting the branch twice.
- Retry only idempotent methods (GET, PUT, DELETE). A POST that returned 5xx may have created the
  resource: the upload path already reads the slot back before deciding, so leave POST out and
  let the caller's read-back handle it. `ponytail:` no per-method policy table, one set.
- On exhaustion raise the last `ASCAPIError`, not `ASCRateLimitError`.
- Log each retry at warning with status and attempt, like the 429 branch.

## Regression test

`backend/tests/test_asc_client.py`:

- a GET answered 500, then 200 returns the 200 body (red before: raises);
- a GET answered 503 for every attempt raises `ASCAPIError` after `_MAX_RETRIES`;
- a POST answered 500 raises at once, one request sent;
- a paginated GET whose second page answers 502 once returns every item.

## Notes

- Related to [bug 006](006-screenshot-sync-apply-silent-past-client-timeout.md): retries make a
  slow row slower, and the progress heartbeat already covers that.
- Not tagged for a release. Mushtra 1.6.0 shipped without it.
