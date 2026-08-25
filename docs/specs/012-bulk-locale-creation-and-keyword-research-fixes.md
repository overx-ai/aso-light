---
id: 012
title: "Bulk locale creation + keyword-research fixes"
status: done
created: 2026-08-24
updated: 2026-08-24
tasks: []
---

# 012 - Bulk Locale Creation + Keyword-Research Fixes

## Problem

A real 36-locale App Store expansion (Refresher, `app_id=1`: 5 live locales → 36) ran head-first into
two blockers and three latent bugs in this codebase. Each one is small; together they are the difference
between "aso-light drives the rollout" and "aso-light watches while fastlane does it".

### The blocker: bulk metadata cannot create a locale

`backend/app/services/metadata/bulk.py` is an **update-only** fan-out. `_build_items` (line ~239) emits

```python
would_skip, reason = True, "no existing version localization to update"
```

for any target locale with no row in `app_metadata_localizations`, and `_is_hard_skip` (line 158) puts
that reason on the un-overridable list — `force=True` cannot bypass it, by design.

That is precisely the case an expansion is made of. Today the only path from 5 locales to 36 is
**62 hand-issued `metadata_create_locale` calls** (31 locales × `app_info` + `version`), each with no
preview, no per-locale char validation, and no resumability. The tool that exists for exactly this shape
of work refuses the job.

### The silent failure: `keywords_suggestions` always returns `[]`

`backend/app/services/keywords/suggestions.py` sends **no headers**. Apple's MZSearchHints endpoint
answers a header-less request with a well-formed plist containing an **empty `<array/>`** — HTTP 200, no
exception, nothing logged. Verified against the exact params the code sends:

```
# code as written                                  → 0 hints
curl ".../hints?clientApplication=Software&media=software&term=breathing&l=en_us"

# + X-Apple-Store-Front: 143441-1,29 (US)          → breathing app, breathing exercises, breathing zone…
# + X-Apple-Store-Front: 143443-1,29 (DE), l=de_de → atemübungen, atemfrequenz-messer katze…
```

The `l=` parameter alone does not select a storefront. The parsing path is fine — `_parse_response`
already falls back to `plistlib.loads` and `data.get("hints", [])` matches the plist key. It is one
missing header, and the failure mode is indistinguishable from "Apple has no suggestions for this term".
No test exercises `ITunesSuggestionsService` today.

### Three latent bugs

- **`keyword_intel_*` MCP tools are mislabeled aliases.** `keyword_intel_refresh`
  (`app/mcp/tools/keywords.py:273`) calls `_refresh_keyword_rankings` — iTunes rank tracking, not intel.
  `keyword_intel_list_for_app` (`:185`) calls `_list_tracked_keywords` and returns rows whose
  `popularity` is always `null`. The real providers (`services/keyword_intel/asa_recommendations.py`,
  `asa_search_terms.py`) and the `keyword_intel_cache` table have **no MCP surface at all** — REST-only,
  as `docs/010-keyword-intelligence.md` admits ("MCP tool mirror is not yet shipped").
- **`FIELD_CHAR_LIMITS` is declared twice** — `services/metadata/validation.py` and
  `services/metadata/translate.py:46`. Two sources of truth that will drift. Worse, `translate.py:140`
  and `:216` enforce the limit with `translated[:char_limit]`, a hard slice that cuts **mid-word**.
  Over a 36-locale fan-out that ships a truncated subtitle to a storefront nobody on the team reads.
- **`search_term_report_rows` is not app-scoped.** `services/asa/analytics.py::_search_term_report_stmt`
  validates `app_id` ownership via `resolve_app`, then never filters on it — the query returns rows for
  every app under the caller's ASA credentials. Tenant isolation holds (credential-scoped, fail-closed
  on `NULL credential_id`); app isolation does not.

## Scope

**In**, in priority order:

1. `bulk_preview` / `bulk_apply` gain `create_missing: bool = False`.
2. `keywords_suggestions` sends `X-Apple-Store-Front`, keyed by country.
3. `keyword_intel_cache` gets a real MCP surface; the two aliases are renamed.
4. `screenshots_list` / `screenshots_upload` / `screenshots_delete` — implement approved spec
   [010](010-mcp-main-listing-screenshots.md), unchanged in scope.
5. Single `FIELD_CHAR_LIMITS`; no mid-word truncation.
6. `_search_term_report_stmt` filters by app.

**Out:**

- **Filling `Keyword.popularity`** (`docs/000-tasks.md` T-006). It has been dead schema since day one and
  should stay dead: Apple exposes no popularity outside ASA recommendations, and the Astro MCP already
  supplies popularity/difficulty/appsCount for any storefront. Duplicating a worse version here is work
  with no payoff. If the column bothers anyone, drop it in a separate migration.
- Apple unified Ads API v1.0 migration (`docs/016`, Jan 2027 sunset). Real, separate.
- Paid intel providers (mobileaction / apptweak / appfigures) — `docs/010` future work.
- App version creation / submit-for-review; app preview videos.

---

## Requirements

### R1 — `create_missing` on bulk metadata

`BulkMetadataService.preview(...)` and `.apply(...)` take `create_missing: bool = False`.

- `BulkPreviewItem` gains `action: Literal["create", "update", "skip"]`. When `create_missing` is true
  and no snapshot row exists, the item is `action="create"`, `current_value=None`, `would_skip=False`.
- **Char validation runs identically on create.** A create item that overflows is still a hard skip —
  `create_missing` must not become a path around `validate_field`.
- **Editability still governs.** `guard.assert_fields_editable` and the
  `editable_fields_json` check in `app_metadata_state` apply unchanged; a field the current version state
  forbids is a hard skip whether the locale exists or not.
- `apply` routes creates through `ASCMetadataService`'s existing create path (the one behind
  `metadata_create_locale`), keeping the **per-locale commit** so a failure at locale 30 does not roll
  back locales 1-29. Creates and updates interleave in one pass.
- `create_missing` defaults to `False`. Existing callers, including the REST router and the frontend,
  must observe no behaviour change.
- The `kind` (`app_info` vs `version`) determines which ASC parent the create targets; both must work.
- `MAX_BULK_TARGET_LOCALES` still caps the fan-out.

**Explicitly not solved here:** partial-failure resume. A run that dies mid-way is re-run with the same
arguments; already-created locales come back as `update` or `unchanged`, which is idempotent enough.

### R2 — `keywords_suggestions` storefront header

- New static map in `backend/app/data/storefronts.py`: ISO country code → iTunes storefront id
  (`us → 143441`, `de → 143443`, …). This is the classic iTunes storefront numbering and is **not** the
  same as `Territory.apple_territory_id`, which is an ASC identifier — do not conflate them.
- `ITunesSuggestionsService.get_suggestions` sends `X-Apple-Store-Front: {id}-1,29`.
- **Signature change**: the parameter becomes `country: str = "us"`, matching `keywords_search`. The
  `l=` locale param is derived (`en_us` style) or dropped. Accept the old `locale=` kwarg for one
  release, mapping `en_us → us`, so the REST endpoint and any stored client calls keep working.
- **An empty result logs a warning** with term + country. This bug survived because a silent `[]` is
  indistinguishable from a legitimately empty answer; that must not be true again.
- Unknown country → fall back to `us` and log.
- `itunes_throttle()` stays where it is.

### R3 — real `keyword_intel` MCP tools

- Rename the two aliases to what they actually call. Note `keywords_refresh_rankings` already exists and
  does the same thing as `keyword_intel_refresh` — the alias is a duplicate and should be **deleted**,
  not renamed, unless a client depends on the name.
- Add tools mirroring `app/api/v1/keyword_intel.py`:
  - `keyword_intel_list(app_id)` → `keyword_intel_cache` rows (keyword, locale, source, `volume_score`,
    `difficulty_score`, `fetched_at`).
  - `keyword_intel_refresh_providers(app_id, provider?)` → runs `_PROVIDERS_FACTORY` and upserts.
- `resolve_app` ownership check on both, like every other app-scoped tool.
- Tool names must satisfy the Anthropic regex — `test_mcp_tool_names.py` already guards this.
- `docs/010-keyword-intelligence.md`'s "MCP tool mirror is not yet shipped" line gets deleted.

### R4 — main-listing screenshots

Implement [spec 010](010-mcp-main-listing-screenshots.md) as written. No scope changes. Called out here
only because a 36-locale rollout needs `screenshots_list` to answer "is this version submittable?"
without opening 36 tabs.

### R5 — one `FIELD_CHAR_LIMITS`, no mid-word truncation

- `translate.py` imports the limits from `validation.py`. Delete the duplicate dict.
- Replace `translated[:char_limit]` at `translate.py:140` and `:216`. On overflow: retry once with an
  explicit "must be under N characters" instruction, then **raise** rather than ship a cut word.
  `_post_process_keywords` is the one exception — the keywords field is comma-separated, so it may drop
  whole trailing terms to fit, never a partial term.
- The limits stay global. Apple counts Unicode code points uniformly; there is no per-locale limit to
  model.

### R6 — app-scope the ASA search-term report

`_search_term_report_stmt` joins through `asa_ad_groups → asa_campaigns` and filters
`asa_campaigns.app_id == app_id`. Credential scoping and the fail-closed `NULL credential_id` behaviour
stay exactly as they are. Add a regression test alongside `test_asa_analytics_scoping.py`.

---

## Files

| Area | File |
|---|---|
| Bulk fan-out | `backend/app/services/metadata/bulk.py`, `backend/app/schemas/metadata.py` |
| Bulk MCP tools | `backend/app/mcp/tools/metadata.py` (`metadata_bulk_preview`, `metadata_bulk_apply`) |
| Bulk REST | `backend/app/api/v1/metadata.py` |
| Suggestions | `backend/app/services/keywords/suggestions.py`, **new** `backend/app/data/storefronts.py` |
| Suggestions callers | `backend/app/mcp/tools/keywords.py:132`, `backend/app/api/v1/keywords.py:89` |
| Keyword intel | `backend/app/mcp/tools/keywords.py:185,273`, `backend/app/api/v1/keyword_intel.py` |
| Screenshots | `backend/app/mcp/tools/screenshots.py`, `backend/app/services/asc/screenshots.py` |
| Char limits | `backend/app/services/metadata/validation.py`, `backend/app/services/metadata/translate.py` |
| ASA scoping | `backend/app/services/asa/analytics.py` |

No migration required — `create_missing` writes to existing tables, and `storefronts.py` is static data.

## Verification

```bash
cd backend && uv run pytest        # all green, incl. test_mcp_tool_names.py
```

New tests:

1. `ITunesSuggestionsService` returns a non-empty list for `("breathing", "us")` and a *different*
   non-empty list for `("atem", "de")` — the second assertion is what proves the header is actually
   selecting a storefront rather than being ignored. Network-marked; skip if offline.
2. `bulk_preview(create_missing=True)` on a locale absent from the snapshot returns `action="create"`,
   `would_skip=False`; with `create_missing=False` the same call returns the existing hard skip.
3. A create item whose value overflows the field limit is still `would_skip=True` even with
   `create_missing=True` **and** `force=True`.
4. `_search_term_report_stmt` returns no rows for an app the search terms don't belong to, while the
   owning app still gets them.
5. `translate` raises on a second overflow instead of returning a truncated string.

End-to-end, against Refresher (`app_id=1`, 5 locales live):

```
metadata_sync(app_id=1)
metadata_bulk_preview(app_id=1, field="keywords", target_locales=[<31 new>], create_missing=True)
  → every item action="create", zero char_overflow_by
metadata_bulk_apply(...same..., create_missing=True)
metadata_get_locale(app_id=1, locale=<each>)   ← read back; a 2xx is not verification
```

## Related

- [010 - Main product-page screenshots over MCP](010-mcp-main-listing-screenshots.md) — R4 is its
  implementation.
- [007 - Metadata editor and cross-localization](007-metadata-editor-and-cross-loc.md) — the bulk
  fan-out this extends.
- [011 - Apple Search Ads Analytics](../011-apple-search-ads-analytics.md) — R6 fixes a scoping bug in
  the search-term report described there.
- `docs/010-keyword-intelligence.md` — the intel table R3 finally exposes.
