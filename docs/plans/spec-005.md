# Plan: spec-005 GDP-Bracket Pricing Strategy

## Approach

After reading (not just grepping) every file the spec's Tasks table names, this
spec is **already fully implemented**. `assign_tier()` matches the spec's exact
priority order (special > manual override > threshold > fallback low),
`GDPBracketConfig` has all four required Pydantic validators (missing tier
price, non-positive tier price, inverted thresholds, alpha-2 normalization),
`GDPFetcher` follows the `IndexFetcher` ABC and is registered in
`IndexRefreshService.fetchers`, `GET /api/v1/indices/gdp` does the sorted
outer-join query the spec describes, `PricePreset.config` is a nullable JSON
column wired through `PresetCreate`/`PresetUpdate`/`PresetResponse` and the
`presets.py` router, and on the frontend `PriceMultiplierPanel` both writes
`config` into `PresetManager`'s `currentSettings` (T11 write side, the one
piece the handoff flagged as unverified) and reads `preset.config` back into
`gdpConfig` on load.

The one real gap is **test coverage, not implementation**: `assign_tier()` and
the `GDPBracketConfig` validators have 18 solid unit tests
(`tests/test_gdp_brackets.py`), but nothing in the suite exercises the
`gdp_brackets` branch of `build_preview_items()` (`_gdp_bracket_items` in
`app/services/pricing/preview.py`) against a real DB session, and nothing
proves the safety-band / price-point-matching reuse holds for that branch
specifically (AC3, AC8). That's the same gap the codebase has for every other
preview branch except `exchange_rate` (`tests/test_preview_endpoint.py`) — it's
not a GDP-specific shortcut, but it's still a real hole for this spec's own
acceptance criteria.

There is no fetcher-level or `GET /indices/gdp`-endpoint-level test anywhere in
the suite (T1/T6), but that matches the codebase's existing convention: no
`IndexFetcher` subclass (PPP/BigMac/Netflix/Spotify) has a fetch-level test,
and no `api/v1/indices.py` route has an endpoint test. Closing that gap would
be scope creep beyond what this spec asks for and beyond what sibling code
does — not attempted here.

## Sequence

1. Run full backend suite to confirm current green state.
2. Add one DB-backed integration test that drives the real
   `preview_subscription_prices` router function (imported directly, same
   pattern as `test_pricing_fixes.py::test_apply_iap_prices_refuses_when_cache_empty`)
   with `index_type="gdp_brackets"`, seeded `EconomicIndex(index_type="gdp_per_capita_ppp")`
   rows spanning top/mid/low/missing-data/special/manual-override territories,
   and a current price far outside the safety band on one territory. Assert:
   - items are correctly bucketed into the right tier price per territory
     (AC3),
   - the far-off territory is flagged `would_be_skipped` via the same
     `exceeds_safety_band` path other strategies use (AC8),
   - `PricePreviewRequest(index_type="gdp_brackets")` without `gdp_config`
     still 422s (AC5, already covered by existing unit test, no new work
     needed).
3. Run full backend suite again; run frontend `tsc --noEmit` and
   `npm test -- --run`.
4. Commit.

## Files

- `backend/tests/test_gdp_bracket_preview_endpoint.py` — new integration test
  (the only file this plan adds).

## Tests first

The new integration test is written to fail if `_gdp_bracket_items` mis-tiers
a territory or if the safety-band check were ever bypassed for this branch —
i.e. it is a regression guard, not a tautology. Ran it against the current
code to confirm it passes without any implementation change (see report).

## Risks

- None identified that require an implementation change. The only risk was
  "PresetManager doesn't actually save config" per the handoff note; verified
  by reading `PriceMultiplierPanel.tsx` lines 345-359, which pass
  `config: isGdpBrackets ? gdpConfig : undefined` into `PresetManager`'s
  `currentSettings` prop, which is spread verbatim into the create-preset
  mutation body. Confirmed correct.

## Deviations

- Spec's Files column for T5 says `api/v1/pricing.py`; actual implementation
  lives in `services/pricing/preview.py` with `api/v1/pricing.py` only
  supplying `build_item`/`raise_error` callbacks. This was already flagged in
  the handoff as an intentional, functionally-equivalent shared-module
  refactor (also used by the MCP tools), not a gap. No action taken.
