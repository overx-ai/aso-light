---
id: 015
title: "MCP: sync a Custom Product Page's screenshots from a local export directory, with replace and delete"
status: done
created: 2026-09-29
updated: 2026-09-29
repo: aso-light
release: 1.6.0
depends_on: [013]
conflicts_with: []
tasks: []
---

# 015 - CPP Screenshots Sync

> **TL;DR** — `cpp_screenshots_sync {app_id, cpp_id, dir}` makes a studio variant export
> (`<dir>/<locale>/NN.png`) a Custom Product Page's screenshots in one call, the way 013 does for the
> main listing: localizations ensured, each display type replaced as a unit, unchanged files skipped by
> MD5, dry run by default. It also adds the missing CPP screenshot delete and stops CPP writes from
> landing on a version that is already in review.

**Prerequisites**: [013 - Screenshots sync from a directory](013-screenshots-sync-from-directory.md)
(its size table, directory scan, root allowlist and plan/apply shape are reused, not copied).

## Problem

Custom Product Pages can only be filled one file at a time today, and only by appending:

- `cpp_upload_screenshot` (`backend/app/mcp/tools/cpp.py:221`) goes through
  `upload_screenshot_to_localization` (`backend/app/services/asc/screenshots.py:324`): find or create the
  set, then `upload_screenshot`. No slot, no replace, no 10-asset cap check, no read-back.
- There is no CPP screenshot delete at all, so a retried upload doubles a locale's set and the only fix is
  the App Store Connect web UI.
- For Mushtra 1.6.0 that is 3 CPPs × 39 locales × 8 slides = 936 base64 calls with no way to retry
  cleanly.
- `ASCCustomProductPageService.get_editable_version_id` (`backend/app/services/asc/cpp.py:286-306`)
  treats `WAITING_FOR_REVIEW` and `IN_REVIEW` as editable (`_EDITABLE_VERSION_STATES`, line 40), and
  falls back to `versions[0]` when no version is editable. A write can therefore target a version Apple
  is reviewing, or a published one.

## Requirements

1. **`cpp_screenshots_sync {app_id, cpp_id, dir, locales?, display_types?, apply=false}`**, with the same
   directory contract as 013:
   - `<dir>/<locale>/*.png|jpg`, ASC locale directories only; dot-directories are skipped;
   - display type from pixel size through 013's one table;
   - filename order;
   - `SCREENSHOT_SYNC_ROOTS` allowlist, after `realpath`.
2. **Localizations are ensured.** A locale directory with no localization on the CPP's editable version
   gets one (`cpp_ensure_localization`'s service call) during `apply`. The dry run reports it as
   `create_localization`.
3. **Replace a type as a unit.** When any file of a type changed, that CPP localization's set ends with
   exactly the directory's files, in its order. Extra assets beyond the directory's count are deleted.
   The existing assets are read back and ordered through `set_screenshot_order`.
4. **Idempotent.** A slot whose asset's `sourceFileChecksum` equals the file's MD5 is `skip`. A second
   `apply` on the same directory makes no write.
5. **Only the types present are touched**; other display types on the CPP are never read for deletion.
6. **Editable version only.**
   - Editable means `PREPARE_FOR_SUBMISSION`, plus `REJECTED` / `DEVELOPER_REJECTED` / `METADATA_REJECTED`
     if ASC reports them for CPP versions. `WAITING_FOR_REVIEW` and `IN_REVIEW` are **not** editable.
   - No fallback to `versions[0]`: no editable version is an error naming the states found.
   - This fixes `get_editable_version_id` for every existing CPP tool, not just the new one.
7. **`cpp_screenshots_delete {app_id, cpp_id, locale, display_type, screenshot_id? | position? | delete_all}`**,
   consent-gated like `screenshots_delete` and `cpp_delete` (`backend/app/mcp/consent.py`).
8. **Dry run is the default**, and the response's final state after `apply` is the read-back inventory
   per locale × type (`count`, `gaps`), as 013 requires.

## Architecture

- Generalise 013's plan/apply over a *localization source*.
  - The main listing resolves `appStoreVersionLocalizations` of the editable app version.
  - A CPP resolves `appCustomProductPageLocalizations` of the CPP's editable version.
  - The set, upload, replace-into-slot, order and read-back helpers in
    `backend/app/services/asc/screenshots.py` already take `localization_type` / `relationship_key`
    (see `find_or_create_screenshot_set`, `upload_screenshot_to_localization`). Reuse them: one plan
    and apply path, two sources, never a copied loop.
- `cpp_screenshots_delete` reuses `delete_screenshot` / `delete_screenshot_set` (`screenshots.py:398,403`).
- MCP tool names use underscores (`test_mcp_tool_names.py`). There is no bare `ValueError`: use the repo's
  error types.

## Edge Cases & Risks

| Case | Impact | Mitigation | Source |
|------|--------|------------|--------|
| CPP version already in review | a write lands on a reviewed page | req 6: refuse, name the state | cpp.py:40-44 |
| Retry after a partial apply | doubled set (today) | MD5 skip + replace-as-unit | req 3-4 |
| New CPP with only the create-time locale | 38 locales missing | ensure localizations during apply | req 2 |
| Delete called without consent | irreversible loss | consent gate like `cpp_delete` | consent.py |
| Directory also holds `findings.json` | error row | top-level files ignored; only locale dirs scanned | 013 req 4 |

## Cross-Repo Interfaces

| Direction | Contract | Pinned version | Owner repo/spec | Fallback until available |
|-----------|----------|----------------|-----------------|--------------------------|
| consumes | studio variant export `projects/<app>/versions/<v>/out/variants/<name>/<locale>/NN.png` | ox-screenshot-blessing `variant_export` as of 2026-09-29 | 4TOOLS/ox-screenshot-blessing | fixture directories in tests |

## Tasks

| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Failing tests first: dry run plans create_localization/replace/skip; apply replaces as a unit and deletes extras; rerun all skip; IN_REVIEW refused; no versions[0] fallback; delete needs consent | dev | — | done | `backend/tests/test_cpp.py`, `backend/tests/test_mcp_screenshots.py` |
| T2 | Fix `get_editable_version_id` and `_EDITABLE_VERSION_STATES` | dev | T1 | done | `backend/app/services/asc/cpp.py` |
| T3 | Localization-source abstraction over 013's plan/apply; CPP source | dev | T1 | done | `backend/app/services/asc/screenshots.py` |
| T4 | `cpp_screenshots_sync` + `cpp_screenshots_delete` tools and schemas | dev | T2, T3 | done | `backend/app/mcp/tools/cpp.py`, `backend/app/schemas/screenshots.py` |
| T5 | Docs: MCP integration doc lists both tools | dev | T4 | done | `docs/007-mcp-integration.md` |

## Acceptance Criteria

- [x] Syncing a 39-locale variant export into a new CPP creates the missing localizations and ends with 8 screenshots per locale, in order.
- [x] A second `apply` reports every row `skip` and makes no write.
- [x] A CPP whose only version is `IN_REVIEW` is refused by every CPP write tool, with the state named.
- [x] `cpp_screenshots_delete` without a fresh consent token is refused.
- [x] All tests passing
- [x] Code review clean
