# 015 — Product Page Optimization (PPO / App Store Version Experiments)

## Context & goal

The sibling of Custom Product Pages ([013](013-custom-product-pages-and-visual-compare.md)):
where a CPP is a *tailored* product page wired to an ASA ad group, **Product Page
Optimization (PPO)** is Apple's built-in **A/B test** of the default product page.
It lets you run up to 3 treatments (variants) against the original — varying
**screenshots, app-preview videos, and app-icon** — and Apple splits live traffic
and measures which converts best. PPO was listed "out of scope" in
[006](006-metadata-editor.md) and never built; this adds it end-to-end,
mirroring the CPP feature so it slots into the existing patterns with no new
infrastructure.

## Decisions (confirmed)
- **Surface:** MCP tools (`experiment_*`) **+** REST (`/apps/{id}/experiments…`) **+** a React "Experiments" page — same three layers as CPP.
- **Scope:** experiment CRUD + lifecycle (submit for review / stop), treatment CRUD (≤3), treatment-localization + **screenshot upload** (reserve→PUT→commit).
- **Results:** **not available via Apple's API** — impressions, conversion rate, and confidence live only in the ASC Analytics UI. The page shows config/state and **deep-links to App Store Connect** for results; there is deliberately no results-reading method/tool.
- **No DB:** like CPP, everything is live ASC calls — no SQLAlchemy models, no migration.

## ASC API shapes (App Store Version Experiments)

**Version split is the key gotcha:** experiment CRUD is on the **v2** app-level
resource; treatments and treatment localizations are **v1**. The `ASCClient`
base URL is `.../v1`; v2 is reached with `BASE_URL.replace("/v1", "/v2")` and an
absolute URL (`ASCExperimentService.base_v2`; same idiom as the IAP flow in
`pricing.py`).

Resource hierarchy:
- `appStoreVersionExperiments` (v2) — attrs `{ name, platform, trafficProportion, startDate (ro), endDate (ro), reviewRequired (ro), state }`; rel `app` (required on create), `appStoreVersionExperimentTreatments`.
  - `GET /v1/apps/{id}/appStoreVersionExperimentsV2` (app-level list — on the v1 host)
  - `POST /v2/appStoreVersionExperiments`, `GET/PATCH/DELETE /v2/appStoreVersionExperiments/{id}`
- `appStoreVersionExperimentTreatments` (v1) — attrs `{ name, appIconName, promotedDate (ro) }`; rel `appStoreVersionExperimentV2` (parent), `appStoreVersionExperimentTreatmentLocalizations`.
  - `GET /v2/appStoreVersionExperiments/{id}/appStoreVersionExperimentTreatments`
  - `POST /v1/appStoreVersionExperimentTreatments`, `PATCH/DELETE /v1/appStoreVersionExperimentTreatments/{id}`
- `appStoreVersionExperimentTreatmentLocalizations` (v1) — attr `{ locale }`; rel `appStoreVersionExperimentTreatment`, `appScreenshotSets`, `appPreviewSets`.
  - `GET /v1/appStoreVersionExperimentTreatments/{id}/appStoreVersionExperimentTreatmentLocalizations`, `POST /v1/appStoreVersionExperimentTreatmentLocalizations` (no PATCH — mutate media via the set children)
- Screenshots reuse the **standard** set/asset model — the same `appScreenshotSets` → `appScreenshots` (reserve → `PUT` to `uploadOperations` → `PATCH uploaded=true`) flow as CPP and the default page, the only difference being the set's parent relationship (`appStoreVersionExperimentTreatmentLocalization`).

**Lifecycle** is driven by PATCHing `state`: submit for review → `WAITING_FOR_REVIEW`, stop a running experiment → `STOPPED`. A live experiment shows `APPROVED` + a populated `startDate`. Other states are server-assigned.

**Constraints enforced/handled:** ≤3 treatments (checked up-front in `create_treatment`, raises `ExperimentLimitError`); one draft experiment per app (Apple returns **409** on a second draft → surfaced as a clean error); delete only **before** start (Apple rejects otherwise → clean error). Locales are App Store locales (`en-US`), **not** alpha-2 territory codes.

**IDOR protection:** handlers verify the parent App (`_get_verified_app` / `resolve_app`) but the raw `experiment_id` / `treatment_id` / `localization_id` are otherwise passed straight to ASC — within one Apple team that would let a caller owning app A touch app B's experiment via A's endpoint. `ASCExperimentService.assert_experiment_in_app` / `assert_treatment_in_experiment` / `assert_localization_in_treatment` (mirroring `ASCPricingService._assert_member`) re-list the parent's children and assert membership before every read/mutate, raising `ChildResourceNotFoundError` → **404** (REST) / `ToolError` (MCP). Because the screenshot-list + direct-upload paths take a `localization_id`, they are nested under experiment/treatment so the full chain is checkable — REST `GET /{app_id}/experiments/{experiment_id}/treatments/{treatment_id}/localizations/{localization_id}/screenshots`, and the `experiment_list_treatment_screenshots` / `experiment_upload_treatment_screenshot` MCP tools take `experiment_id` + `treatment_id` alongside `localization_id`.

## DRY: shared screenshot machinery
The `appScreenshotSets`/`appScreenshots` upload + shaping is now in
`backend/app/services/asc/screenshots.py` (parent-agnostic:
`upload_screenshot`, `find_or_create_screenshot_set`, `fetch_screenshot_sets`,
`build_source_url`) and the `Screenshot`/`ScreenshotSet` schemas +
`screenshotDisplayType` validation in `backend/app/schemas/screenshots.py`.
Both `ASCCustomProductPageService` (CPP) and `ASCExperimentService` (PPO)
delegate here; CPP's public methods were refactored to thin delegators (behaviour
unchanged — `tests/test_cpp.py` still green).

## Implementation map

**Backend**
- `backend/app/services/asc/experiment.py` — `ASCExperimentService(client)`: `list_experiments`, `get_experiment`, `create_experiment`, `update_experiment` (+ `submit_experiment_for_review`, `stop_experiment`), `delete_experiment`; `list_treatments`, `create_treatment` (≤3 guard), `update_treatment`, `delete_treatment`; `list_treatment_localizations`, `find_or_create_localization_id`, `delete_treatment_localization`; `get_treatment_screenshots`, `upload_screenshot_to_treatment`, `populate_treatment_from_upload` (ensure loc + upload set, cleans up a *created* localization on failure).
- `backend/app/schemas/experiment.py` — request/response models + `shape_experiment` / `shape_treatment` helpers (shared by MCP + REST), `MAX_TREATMENTS`, `SETTABLE_EXPERIMENT_STATES`.
- `backend/app/mcp/tools/experiment.py` — `experiment_list/get/create/update/submit_for_review/stop/delete`, `experiment_list_treatments/create_treatment/update_treatment/delete_treatment`, `experiment_ensure_treatment_localization`, `experiment_list_treatment_screenshots`, `experiment_upload_treatment_screenshot` (MCP tool names use underscores, not dots — the Anthropic tool-name regex rejects `.`). Registered in `backend/app/mcp/server.py`.
- `backend/app/api/v1/experiment.py` — per-app REST routes under `/apps` (registered in `backend/app/api/v1/__init__.py`). Reuses `_read_upload_payload` from `cpp.py` for the multipart `from-upload` route.

**Frontend**
- `frontend/src/lib/experiment-hooks.ts` — TanStack Query hooks (shared `api` client, namespaced keys, Mantine notifications).
- `frontend/src/pages/Experiments.tsx` — experiments table (name / state badge / traffic %), create-experiment modal, submit/stop/delete actions, a "Treatments" modal (create ≤3, delete, per-treatment locale+device screenshot upload), and a **results-not-available callout deep-linking to App Store Connect**.
- Route `apps/:id/experiments` in `frontend/src/App.tsx`; nav entry in `frontend/src/components/AppNavItem.tsx`.

## Tests & verification
- `backend/tests/test_experiment.py` (mirrors `tests/test_cpp.py`): asserts the v1/v2 endpoint split, request bodies + relationships, the ≤3-treatment guard, state-transition PATCH bodies, and from-upload cleanup-on-failure (created vs reused localization).
- Regression: `cd backend && uv run pytest` (CPP refactor keeps `tests/test_cpp.py` green); `uv run ruff check`.
- Frontend: `npx tsc --noEmit`; `make dev` → open `/apps/:id/experiments`.
- MCP smoke: `uv run python -c "import asyncio; from app.mcp.server import mcp; print([t.name for t in asyncio.run(mcp.list_tools()) if t.name.startswith('experiment_')])"`.

## Known limitations
- **No programmatic results.** The API has no results/metrics endpoint; results are UI-only (ASC → Analytics → Product Pages). The page deep-links there.
- **App previews** (`appPreviewSets`) are not yet uploaded through the UI — only screenshots. The video upload flow can be added later using the same shared media path.
- **Alt app icons** must already ship in the published build; a treatment references an icon by `appIconName`, it does not upload it.
