---
id: 016
title: "MCP: sync a Product Page Optimization treatment's screenshots from a local export directory"
status: in-progress
created: 2026-09-30
updated: 2026-09-30
repo: aso-light
release: 1.6.0
depends_on: []
conflicts_with: []
tasks: []
---

# 016 - Experiment Treatment Screenshots Sync

> **TL;DR** — `experiment_screenshots_sync {app_id, experiment_id, treatment_id, dir}` loads a studio
> variant export (`<dir>/<locale>/NN.png`) into one PPO treatment in a single call. It works like
> `cpp_screenshots_sync`: treatment localizations are ensured, each display type is replaced as a unit,
> unchanged files are skipped by MD5, and it is a dry run by default. A treatment in an experiment that
> Apple is reviewing or running is refused.

**Prerequisites**:
- [013 - Screenshots sync from a directory](013-screenshots-sync-from-directory.md) and
  [015 - CPP screenshots sync](015-cpp-screenshots-sync.md): their scan, plan, apply, allowlist and
  one-apply guard are reused, not copied.
- [docs/015 - Product Page Optimization](../015-product-page-optimization.md) for the experiment
  resources.

## Problem

- An A/B test of screenshot sets is a PPO experiment (1 original + up to 3 treatments, with Apple
  splitting organic traffic), not a Custom Product Page. A CPP only gets the traffic you send it.
- Treatments can only be filled one base64 file at a time today, through
  `experiment_upload_treatment_screenshot`, and only by appending. There is no replace, no MD5 skip and
  no read-back.
- Mushtra's 1.6 test is 3 treatments × 39 locales × (8 iPhone + 7 iPad), which is about 1,750 calls
  with no clean retry.

## Requirements

1. The tool `experiment_screenshots_sync {app_id, experiment_id, treatment_id, dir, locales?, display_types?, apply=false}`
   returns the same `ScreenshotSyncResult` as `cpp_screenshots_sync`.
   - `version_id` is the treatment id.
   - The same rows are possible: `skip | replace | upload | create_localization | error`.
2. Membership is checked before any read of the treatment:
   - the experiment belongs to the app (`assert_experiment_in_app`);
   - the treatment belongs to the experiment (`assert_treatment_in_experiment`);
   - either failure is a `ToolError`, and nothing is written.
3. The experiment state must be one of `PREPARE_FOR_SUBMISSION`, `READY_FOR_REVIEW` or `REJECTED`.
   - Any other state is refused by name, with the editable states listed.
   - This happens before the scan, so no write lands on an experiment in review or running.
4. A missing treatment localization is planned as `create_localization` and created on apply. It must
   be one of the app's own locales.
5. Everything else comes from 013/015 unchanged: the directory rules, the size table,
   `SCREENSHOT_SYNC_ROOTS` (a refused dir makes no ASC call), the ten-file cap, display types never
   touched outside the filter, per-row progress, and one apply per target (keyed by the treatment).
6. No `missing_families` for treatments.
   - A treatment is judged against the original by Apple, not against the listing's families.
   - The caller syncs every family it wants shown.

## Design

- `TreatmentScreenshotService(LocalizationScreenshotService)` in `backend/app/services/asc/experiment.py`:
  - `localization_type = appStoreVersionExperimentTreatmentLocalizations`;
  - `set_relationship = appStoreVersionExperimentTreatmentLocalization`;
  - `localizations_by_locale` and `ensure_localization` delegate to `ASCExperimentService`;
  - `editable_treatment(asc_app_id, experiment_id, treatment_id) -> EditableVersion` runs Requirements 2–3.
- `ExperimentNotEditableError(NotEditableError)`, which `asc_tool_error` already maps to `ToolError`.
- The tool lives in `backend/app/mcp/tools/experiment.py`. It is a `bind` that returns a `SyncTarget`
  (the treatment as the version, the app's locales as `creatable`) and calls `run_screenshot_sync`.

## Tasks

| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Red: treatment sync tests (below), failing on the missing tool | dev | — | done (11 red: tool not registered) | `backend/tests/test_mcp_screenshots.py` |
| T2 | Green: service, error, tool | dev | T1 | done (11 green; suite 528 passed, bug 005's pinned test excepted) | `backend/app/services/asc/experiment.py`, `backend/app/mcp/tools/experiment.py` |
| T3 | Docs: PPO doc section, MCP reference, changelog | dev | T2 | open | `docs/015-product-page-optimization.md`, `docs/007-mcp-integration.md`, `docs/000-changelog.md` |

## Tests (T1, all red before T2)

These go in `backend/tests/test_mcp_screenshots.py`, next to the CPP sync tests, and reuse `FakeASC`.
`FakeASC` gains the experiment, treatment and treatment-localization routes.

- `test_treatment_sync_dry_run_plans_create_localization_replace_and_skip`
- `test_treatment_sync_apply_replaces_as_a_unit_and_creates_the_missing_localization`
- `test_treatment_sync_rerun_is_all_skip_with_zero_writes`
- `test_treatment_sync_never_touches_another_display_type`
- `test_treatment_sync_refuses_an_experiment_that_is_not_editable_before_any_write` (WAITING_FOR_REVIEW, IN_REVIEW, APPROVED, STOPPED)
- `test_treatment_sync_refuses_a_treatment_of_another_experiment`
- `test_treatment_sync_refuses_an_experiment_of_another_app`
- `test_treatment_sync_refuses_a_dir_outside_the_allowlist_before_any_asc_call`

## Acceptance Criteria

- [ ] Every T1 test was red before T2 and is green after it.
- [ ] The full suite is green, apart from bug 005's pinned test.
- [ ] A live dry run on a Mushtra treatment plans 8 iPhone uploads per locale; after an apply, a
      re-run is all skip.
