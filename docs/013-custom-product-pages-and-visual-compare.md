# 013 — Custom Product Pages + Visual Old-vs-New Screenshot Compare

## Context & goal

Driven by a real problem on **Refresher (ai.overx.refresher)**: App Store conversion ~3.45% and Apple Search Ads TTR ~3.1% — the screenshots are the bottleneck. We need ASO-Light to (a) let us **see old-vs-new screenshots side by side** to judge creative before shipping, and (b) manage **Custom Product Pages (CPPs)** and wire them to ASA ad groups so each ad group serves a tailored, higher-converting page.

CPP is the backbone: the "new" set in the visual comparison **is** a CPP's screenshot set, and CPPs get assigned to ASA ad groups.

## Decisions (confirmed)
- **Compare surface:** MCP tool (returns a composited before/after image) **+** a React "Compare" page in the web UI.
- **Comparison source:** "old" = the **default** product page screenshots (live), "new" = a selected **Custom Product Page**'s screenshot set.
- **CPP scope:** full **CRUD + screenshot management + ASA wiring** (link a CPP to specific ad groups).

## Architecture recon (current state)
- **Backend:** FastMCP ≥2 + FastAPI; tools in `backend/app/mcp/tools/<domain>.py`, registered in `backend/app/mcp/server.py`. Services in `backend/app/services/`. Pydantic schemas in `backend/app/schemas/`. Postgres + Alembic. `uv`, Python ≥3.12.
- **ASC client:** `backend/app/services/asc/client.py` — ES256 JWT (PyJWT), 150ms throttle + 429 backoff, helpers `_get`, `_post`, `_patch`, `_delete`, `_get_all_pages`. v2 reachable via `BASE_URL.replace("/v1","/v2")` (see `pricing.py` IAP flow).
- **MCP context:** `backend/app/mcp/context.py` — `session_scope()`, `resolve_app(app_id, session)`, `get_user_id()`, `_get_asc_client_for_app(...)`; raise `fastmcp.exceptions.ToolError` for user-visible errors.
- **Screenshots today:** only subscription/IAP **review** screenshots (3-step reserve→PUT→commit). **No marketing-screenshot fetch, no image compositing (no Pillow), no CPP code.** FastMCP supports a native `Image` return type (not yet used).
- **Frontend:** React 19 + Vite + Mantine v8 + TanStack Query; all API calls go through `frontend/src/lib/hooks.ts`; pages under `frontend/src/pages/`. REST under `/api/v1/...` (JWT); MCP at `/mcp/` (PAT).

## ASC API shapes (Custom Product Pages)
Resource hierarchy (App Store Connect API):
- `appCustomProductPages` — attrs `{ name, visible }`; rel `app` (required on create), `appCustomProductPageVersions`.
  - `POST /v1/appCustomProductPages` `{data:{type, attributes:{name,visible}, relationships:{app:{data:{type:"apps",id}}}}}`
  - `GET /v1/apps/{id}/appCustomProductPages`, `GET/PATCH/DELETE /v1/appCustomProductPages/{id}`
- `appCustomProductPageVersions` — attrs `{ version (ro), state (ro), deepLink }`; rel `appCustomProductPage`, `appCustomProductPageLocalizations`.
  - `GET /v1/appCustomProductPages/{id}/appCustomProductPageVersions`, `POST /v1/appCustomProductPageVersions`
- `appCustomProductPageLocalizations` — attrs `{ locale, promotionalText }`; rel `appCustomProductPageVersion`, `appScreenshotSets`, `appPreviewSets`.
  - `GET /v1/appCustomProductPageVersions/{id}/appCustomProductPageLocalizations`, `POST /v1/appCustomProductPageLocalizations`
- Screenshots reuse the **standard** set/asset model: `appScreenshotSets` (attr `screenshotDisplayType`, rel `appCustomProductPageLocalization`, `appScreenshots`) → `appScreenshots` (3-step reserve→`PUT` to `uploadOperations`→`PATCH uploaded=true`). The DEFAULT page's screenshots live under `appStoreVersionLocalizations/{id}/appScreenshotSets` — add a fetch helper for both.

## ASA → CPP assignment (Apple Search Ads Campaign Management API)
- A Custom Product Page ad is an **Ad** inside an ad group that references the CPP id. Endpoint family: `POST /campaigns/{campaignId}/adgroups/{adGroupId}/ads` with the creative referencing `productPageId` (the CPP id) — confirm exact body against the ASA `Ad` schema during impl (`backend/app/services/asa/` already has the authed ASA client + ad-group tools to mirror). List/patch/delete ads on the ad group to manage the link.

---

## Phase A — CPP backend foundation (verifiable, self-contained)
**New files:**
- `backend/app/services/asc/cpp.py` — `ASCCustomProductPageService(client)` with: `list_cpps(asc_app_id)`, `get_cpp(cpp_id)`, `create_cpp(asc_app_id, name, visible)`, `update_cpp(cpp_id, name?, visible?)`, `delete_cpp(cpp_id)`, `list_versions(cpp_id)`, `list_localizations(version_id)`, `get_cpp_screenshots(localization_id)` (sets + assets), and `get_default_screenshots(version_localization_id)` for the default page.
- `backend/app/schemas/cpp.py` — `CPPResponse`, `CPPVersion`, `CPPLocalization`, `ScreenshotSet`, `Screenshot{id, file_name, source_url, display_type}` Pydantic models.
- `backend/app/mcp/tools/cpp.py` — `@mcp.tool` wrappers: `cpp.list`, `cpp.get`, `cpp.create`, `cpp.update`, `cpp.delete`, `cpp.list_screenshots`. Use `resolve_app`/`session_scope`/`get_user_id`; convert `HTTPException`→`ToolError`.
- Register the module import in `backend/app/mcp/server.py`.

**Verify:** `cd backend && uv run python -c "import asyncio; from app.mcp.server import mcp; print([t.name for t in asyncio.run(mcp.list_tools()) if t.name.startswith('cpp.')])"` ; `uv run pytest`.

## Phase B — Screenshot upload to a CPP
- Extend `ASCCustomProductPageService` with the 3-step screenshot upload to a CPP localization's `appScreenshotSets`/`appScreenshots` (mirror the review-screenshot flow in `pricing.py`). MCP tool `cpp.upload_screenshot(cpp_id, locale, display_type, file_base64, file_name)`.

## Phase C — Visual compositor + compare tool
- Add **Pillow** to `backend/pyproject.toml`.
- `backend/app/services/visual/compare.py` — fetch default-page screenshots (old) + a CPP's screenshots (new) for a locale/device, download the image assets, and composite a 2-row BEFORE/AFTER montage (per-image labels + titles). Return PNG bytes.
- MCP tool `screenshots.compare(app_id, cpp_id, locale, device)` → returns a FastMCP `Image` (composited PNG) so agents can view it.
- REST `GET /api/v1/apps/{app_id}/screenshots/compare?cpp_id=&locale=&device=` → PNG, for the web UI.

## Phase D — React Compare page + CPP management UI
- `frontend/src/pages/Compare.tsx` (route + nav entry): app + locale + device + CPP pickers; renders the composited before/after image; a CPP list/create/edit panel.
- Hooks in `frontend/src/lib/hooks.ts` (TanStack Query) for the new REST endpoints. Mantine components, matching existing pages.

## Phase E — ASA wiring
- `backend/app/services/asa/` + `asa.py` tools: `asa.list_cpp_ads(campaign_id, adgroup_id)`, `asa.assign_cpp(campaign_id, adgroup_id, cpp_id)` (create/patch the Ad referencing the CPP), `asa.unassign_cpp(...)`. Surface in the ASA/paid-search frontend page.

## Phase F — tests + docs
- `backend/tests/test_cpp.py` (service + tool ownership), `test_visual_compare.py` (compositor with sample images). Update `docs/INDEX.md`. Frontend vitest for the Compare page.

## Notes / risks
- ASC marketing-screenshot fetch is new to this codebase — reuse the authed `ASCClient` + `_get_all_pages`; the image asset URL is built from `imageAsset.templateUrl` (`{w}`/`{h}`/`{f}` substitution) — download via plain httpx (no auth needed for the CDN URL).
- Confirm the exact ASA Ad/CPP body against the live ASA `Ad` schema before Phase E.
- CPP create auto-creates a draft version; localizations are added under that version.
