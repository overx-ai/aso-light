---
id: 005
title: "ASA sync: every re-sync duplicates metric rows, because a NULL storefront never conflicts"
status: open
severity: high
created: 2026-09-29
updated: 2026-09-29
source: tick
repo: aso-light
files: backend/app/services/asa/sync.py, backend/app/models/asa.py, backend/alembic/versions/
---

# BUG 005 - ASA metric re-sync duplicates NULL-storefront rows

> **TL;DR** — `ASAMetricDaily`'s upsert conflicts on `(dim_kind, dim_id, date, storefront)`,
> but every row stores `storefront = NULL`, and NULL never equals NULL in a unique index. The
> upsert therefore degrades to an INSERT, and each sync multiplies impressions, taps, installs and
> spend in the rollups. The reproduction has been committed (red) since d6850ab.

## Symptom

`backend/tests/test_asa_metric_upsert_dedupe.py::test_resync_does_not_duplicate_null_storefront_rows`
fails on `main` (b353bb8), and has since d6850ab (2026-08-27):

```
AssertionError: re-sync duplicated the grain: 2 rows for one (dim_kind, dim_id, date) -- every sync inflates the rollups
```

**Repro (expected → actual):** run `_upsert_metrics` twice with the same campaign-day row, which
has no `countryOrRegion` (so `storefront = None`). Expected: 1 row with spend 25.50. Actual: 2 rows,
and the `func.sum` rollups double.

It hits every metric row: `reports._selector` sends no `groupBy`, so Apple never returns
`countryOrRegion`.

## Root cause

`_upsert_metrics` (`backend/app/services/asa/sync.py:122-147`) uses
`on_conflict_do_update(index_elements=["dim_kind", "dim_id", "date", "storefront"])`. The unique
constraint on `ASAMetricDaily` (`backend/app/models/asa.py`, around line 351) includes the nullable
`storefront`. SQLite and PostgreSQL both treat NULLs as distinct in a unique index, so two
NULL-storefront rows at the same grain never conflict.

## Fix

(to fill) Candidate fixes: store a non-NULL sentinel for "all storefronts" (e.g. `''`), with a
migration that backfills it and collapses the existing duplicates; or use PostgreSQL 15's
`NULLS NOT DISTINCT`, plus a SQLite-compatible equivalent. Existing duplicate rows must be
deduplicated in the same migration, or the rollups stay inflated.

## Regression test

The existing red test above.
