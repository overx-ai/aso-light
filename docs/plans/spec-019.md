---
item: docs/specs/019-listing-optimizer.md
kind: spec
created: 2026-10-28
lane: go
---

## Approach
One `ListingOptimizerService` in `services/listing/service.py` orchestrates 014's pieces
(`own_listing_terms` / `listing_terms_from_lookup` for `using`, `score_terms` + `seed_expansion` +
title mining for candidates, `pick_best_variant` for per-locale variants, `build_pack` for the
localized keyword field) and the metadata stack (`translate_with_cache`, `classify_keyword`,
bulk-preview payload shape). Brainstorm results and per-locale variants are stored as
`keyword_intel_cache` rows under two new sources, so no new candidate table is needed; the only new
table is a per-app `listing_drafts` row holding the last localized proposal for `source=proposal`
export. Proposals reuse the translator stack through a new `listing_proposals` field kind, so the
existing cache and per-app monthly cap bill them unchanged; pure validators drop invalid sets.
Rejected: a new `AbstractTranslator.complete()` method for free-form prompts — every translator
impl and the cap/cache path would need a parallel copy.

## Sequence
1. Tests first (red): pure validators, coloring `missing_localized`, service with mocked hints/LLM,
   localize editable-fields filter, export, app scope.
2. `coloring.classify_keyword` variant + `missing_localized`; tracker `MAX_TRACKED_TERRITORIES` cap.
3. `translate.py` `listing_proposals` field kind; 014 extraction gains `include_description`.
4. `ListingDraft` model + migration; `schemas/listing.py`; `services/listing/`.
5. REST `api/v1/listing.py`, `GET .../metadata/export`, background brainstorm after apps sync (REST + MCP).
6. MCP `listing_*`, `metadata_export`; consent classification.
7. Frontend types, hooks, `ListingOptimizer.tsx`, route + nav.

## Files
| File | Change |
|---|---|
| `backend/app/services/listing/__init__.py`, `service.py`, `proposals.py`, `background.py` | new: service, pure proposal validation/coverage, post-sync scheduler |
| `backend/app/schemas/listing.py` | new: request/response models |
| `backend/app/models/listing_draft.py`, `backend/app/models/__init__.py` | new `ListingDraft` |
| `backend/alembic/versions/e7c1a3b5d9f2_listing_drafts.py` | new migration |
| `backend/app/services/metadata/coloring.py` | variant match + `missing_localized` |
| `backend/app/services/keywords/tracker.py` | `MAX_TRACKED_TERRITORIES` cap |
| `backend/app/services/metadata/translate.py` | `listing_proposals` field kind + prompt |
| `backend/app/services/keyword_intel/free.py` | `include_description` on listing extraction |
| `backend/app/api/v1/listing.py`, `__init__.py`, `metadata.py`, `apps.py` | routes, export, post-sync brainstorm |
| `backend/app/mcp/tools/listing.py`, `apps.py`, `server.py`, `consent.py` | tools + classification |
| `backend/tests/test_listing_optimizer.py` | new tests |
| `frontend/src/types/index.ts`, `src/lib/hooks.ts`, `src/pages/ListingOptimizer.tsx`, `src/App.tsx`, `src/components/AppNavItem.tsx` | UI |

## Tests first
- `missing` never contains a `using` term (mocked hints/search).
- `missing_localized` fires for DE when the source term ranks low and the variant is absent; not when
  the variant is in the listing.
- Proposal validation: over-limit name/subtitle/keywords and cross-field word repeats dropped; every
  returned proposal passes; malformed LLM output yields no proposals.
- Localize with `READY_FOR_DISTRIBUTION` state (`editable_fields == ["promotional_text"]` plus app-info
  fields absent) emits no payload outside `editable_fields`.
- Export returns per-locale fields for `current` and overlays the draft for `proposal`.
- Another user's app is rejected on a listing route.

## Risks
- Hint budget: brainstorm scores at most `MAX_FREE_TERMS` terms and picks variants for at most
  `BRAINSTORM_VARIANT_TERMS` x `MAX_PACK_LOCALES`; localize processes `MAX_PACK_LOCALES` locales per
  call and returns `remaining_locales` (the UI loops). Worst case still ~1–2 min per call when
  variants miss and walk the full prefix depth.
- LLM JSON may be malformed or fenced; parsed leniently, invalid sets dropped, never repaired.
- Rankings outside US exist only after the listing rank refresh; a market with no ranking row is
  treated as "ranks poorly".

## Deviations
- MCP classification: only `listing_gaps` and `metadata_export` are `READ_ONLY`. `listing_brainstorm` (stores candidates, bills variant translations), `listing_propose` and `listing_localize` (bill LLM calls) stay unclassified, like `metadata_translate` / `keyword_intel_pack_fields`; the spec said "all read-only".
- Candidates and per-locale variants are `keyword_intel_cache` rows under sources `listing_brainstorm` (locale = storefront, `extra.status` using/missing/candidate) and `listing_variant` (locale = target locale, `extra.best`). They are visible in `keyword_intel_list` under those source labels.
- New `listing_drafts` table (+ migration `e7c1a3b5d9f2`) holds the last localized proposal so `GET .../metadata/export?source=proposal` has something to overlay.
- Localize handles the source locale plus `MAX_PACK_LOCALES` (4) other locales per call and returns `remaining_locales`; the UI hook loops until done ("one click" = several bounded requests).
- The gap grid's rows are tracked keywords (the only terms with rankings). Brainstorm picks variants for tracked + top missing terms, capped at `BRAINSTORM_VARIANT_TERMS` (5) x `MAX_PACK_LOCALES` locales of a different language.
- Rank refresh across live storefronts is `POST /apps/{id}/listing/rankings/refresh` (REST + UI) only. MCP still has `keywords_refresh_rankings` (US); `listing_gaps` treats a market with no ranking row as unranked.
- Another user's app returns 403, not 404: the shared `_get_verified_app` behaviour, left unchanged. The test accepts either.
- 014's MCP `variants_for` closure moved into `translate.keyword_variants` so pack_fields and the listing service share it. `own_listing_terms` / `listing_terms_from_lookup` gained `include_description` (seeds = name/subtitle/keywords only).
- Post-sync brainstorm: REST uses `BackgroundTasks` (runs after the response); MCP `apps_sync` uses `schedule_brainstorm` (an `asyncio` task scheduled after its session commits). Both skip apps that already have a brainstorm.
- Docs (003, 007, CLAUDE.md tool count) left to /docs.
