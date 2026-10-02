---
id: 010
title: "experiment_submit_for_review and experiment_stop PATCH `state`, which Apple refuses"
status: open
severity: high
created: 2026-10-02
updated: 2026-10-02
source: manual
repo: aso-light
release: 1.6.0
files: backend/app/services/asc/experiment.py, backend/app/mcp/tools/experiment.py, backend/app/api/v1/experiment.py, backend/app/mcp/consent.py, backend/tests/test_experiment.py
---

# BUG 010 - An experiment cannot be submitted or stopped: `state` is read-only

> **TL;DR** — `experiment_submit_for_review` PATCHed `state: WAITING_FOR_REVIEW` onto the experiment, and
> `experiment_stop` PATCHed `state: STOPPED`. Apple refuses both: "The attribute 'state' can not be included
> in a 'UPDATE' operation". The unit tests passed because they asserted the PATCH body against a fake, not
> Apple's schema. A test is submitted through a review submission, like a CPP, and stopped with `started:
> false`.

## Symptom

On 2026-10-02 the Mushtra test «1.6 screenshots» was submitted (`e4b512c8-…`, 3 treatments, 1.6.0 live):

```
experiment_submit_for_review → ASC API error: The attribute 'state' can not be included in a 'UPDATE' operation
```

**Reproduction:** any `experiment_submit_for_review` on a `PREPARE_FOR_SUBMISSION` experiment. The same
applies to `experiment_stop`, and to `experiment_update {state}` / REST `PATCH {state}`.

## Root cause

- `ASCExperimentService.update_experiment` sent `state`, and the submit and stop helpers were built on it.
- Apple's `AppStoreVersionExperimentV2UpdateRequest` attributes are only `name`, `trafficProportion` and
  `started`.
- Submitting is `reviewSubmissions` + `reviewSubmissionItems` (relationship `appStoreVersionExperimentV2`),
  then `PATCH reviewSubmissions/{id} {submitted: true}`.

## Fix

- **`submit_experiment_for_review(asc_app_id, experiment_id)`:**
  - It finds the app's open `READY_FOR_REVIEW` submission for the experiment's platform, or creates one.
  - It adds the experiment as an item, then submits.
  - An open submission that already holds other items is refused (409, naming it). Submitting it would send
    those items too, such as a CPP someone added in App Store Connect. Items are read with
    `include=appStoreVersionExperimentV2`; one without that linkage counts as another item.
  - An open submission whose only item is this experiment is a retry after a failed `submitted` PATCH: it is
    submitted without adding the item again.
  - `UNRESOLVED_ISSUES` submissions are left alone. If Apple refuses a new submission because of one, its
    error is surfaced.
- **`start_experiment` / `stop_experiment`:** `started: true / false`. There is a new consent-gated MCP tool,
  `experiment_start`, for a test Apple approved.
- **`update_experiment`:** takes `started` instead of `state`.
  - The MCP `experiment_update` loses `state`, since lifecycle has its own consent-gated tools.
  - The REST `PATCH {state}`, which the web UI uses, now routes `WAITING_FOR_REVIEW` to the submit and
    `STOPPED` to the stop.

## Regression test

`backend/tests/test_experiment.py`, all red first:

- `test_submit_for_review_goes_through_a_new_review_submission`
- `test_submit_for_review_reuses_an_empty_open_submission`
- `test_submit_for_review_reads_the_open_submission_for_the_experiments_platform`
- `test_submit_for_review_retry_resubmits_a_submission_already_holding_this_experiment`
- `test_submit_for_review_refuses_an_open_submission_holding_other_items[cpp|another-test|ours-and-a-cpp|unreadable]`
- `test_start_and_stop_patch_started_never_state[stop|start]`
- `test_rest_patch_state_waiting_for_review_goes_through_a_review_submission`,
  `test_rest_patch_state_stopped_sets_started_false`, `test_rest_patch_with_nothing_to_update_is_a_400`,
  `test_rest_submit_into_a_busy_submission_is_a_409_naming_it`

Two tests that pinned the wrong PATCH body were replaced.
