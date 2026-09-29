---
id: 006
title: "screenshots_sync / cpp_screenshots_sync apply send no progress, so a client aborts a long run at 300 s and loses the result"
status: fixed
severity: high
created: 2026-09-29
updated: 2026-09-29
source: tick
repo: aso-light
release: 1.6.0
files: backend/app/mcp/tools/screenshots.py, backend/app/mcp/tools/cpp.py, backend/app/services/asc/screenshots.py, backend/app/mcp/progress.py, backend/tests/test_mcp_screenshots.py
---

# BUG 006 - Screenshot sync apply is silent past the client's 300 s timeout

> **TL;DR** — An applied `screenshots_sync` / `cpp_screenshots_sync` over a full studio export runs
> for many minutes and sends nothing until it returns. Claude Code aborts any tool call that is
> silent for 300 s. The server keeps uploading, but the caller never sees the result, and a caller
> that retries starts a second apply against the same page while the first is still writing it.

## Symptom

- A 39-locale export is 312 uploads, about 23 s per locale: the apply takes roughly 15 minutes.
- `run_screenshot_sync` (`backend/app/mcp/tools/screenshots.py`), the one path behind both tools,
  never touches the FastMCP `Context`, so no `notifications/progress` is sent while it runs.
- Claude Code aborts the call: `sent no response or progress for 300s; aborting`.
- The server finishes the run anyway. The caller loses the rows, the read-back counts and the
  inventory, and has to re-derive them with `screenshots_list`.
- A retry is a second apply on the same page while the first is still deleting and uploading.
  Both plan from the same stale state, and both write the same sets.

**Reproduction (2026-09-29, live):** `cpp_screenshots_sync {apply: true}` for app 4 was aborted
at 300 s with 13 of 39 locales done.

**Expected → actual:** a caller that sent a `progressToken` receives a progress notification for
every locale x display-type row, plus a heartbeat at least every 30 s while one row is slow. It
receives none.

## Root cause

`run_screenshot_sync` awaited `LocalizationScreenshotService.apply_sync` as one opaque call: no
tool took the FastMCP `Context`, and `apply_sync` had no hook between rows, so nothing could
report while it ran. Nothing tracked a running apply either, so a retried call planned and wrote
the same page concurrently.

## Fix

- `app/mcp/progress.py` — `ProgressReporter(ctx, total)`: `advance(message)` reports
  `done/total` after each unit, and a background heartbeat repeats the last state every
  `PROGRESS_HEARTBEAT_SECONDS` (30) while one unit runs long. Without a context it does nothing;
  `Context.report_progress` itself is a no-op when the client sent no `progressToken`. A failed
  send is logged and swallowed, so a client that went away cannot stop a half-applied set between
  a delete and its upload.
- `LocalizationScreenshotService.apply_sync(..., on_step=None)` calls `on_step` after each row,
  skipped rows included. The service layer stays MCP-agnostic.
- `screenshots_sync` and `cpp_screenshots_sync` take `ctx: Context | None = None`. FastMCP
  injects it and keeps it out of the input schema. Both pass it to `run_screenshot_sync`, which
  reports `0/N` at the start and one report per locale x display-type row. The post-apply
  read-back runs inside the same reporter, so the heartbeat covers it too.
- One apply per page: `_one_apply_per_page` keys the target's ASC version id (unique across
  ASC, so two local app rows for one ASC app share it), so the main page and each CPP version
  are separate keys. It is taken before the plan, since a plan read while another apply writes
  the page is stale, and a second apply on the same page is refused with a `ToolError` that
  names the page before it reads anything. The guard is released on success, failure and
  cancellation. Dry runs are not guarded. The guard is in-process only (`ponytail:`), which fits the
  backend's single worker.

## Regression test

`backend/tests/test_mcp_screenshots.py`, section "Sync apply progress + one apply per page (bug 006)":

- `test_sync_apply_reports_progress_once_per_row`, `test_cpp_sync_apply_reports_progress_once_per_row`:
  a recording context sees `(0,3) (1,3) (2,3) (3,3)`. Red before the fix: the tools took no `ctx`.
- `test_sync_progress_reaches_a_real_mcp_client`: an in-memory `fastmcp.Client` with a progress
  handler receives the same four notifications, which proves FastMCP injects the context.
- `test_sync_heartbeat_reports_while_one_row_is_slow`: rows slower than the interval produce
  repeat reports.
- `test_sync_dry_run_and_no_context_report_nothing`, `test_sync_context_is_not_a_tool_argument`,
  `test_sync_progress_survives_a_client_that_went_away`.
- `test_a_second_apply_on_the_same_page_is_refused_while_one_runs` (red before: the second
  apply ran and blocked; it also asserts the refused call never planned),
  `test_the_apply_guard_is_released_when_an_apply_fails`,
  `test_a_cancelled_apply_releases_the_guard_and_stops_the_heartbeat`.
