---
status: current
created: 2026-10-03
updated: 2026-10-03
---

# 021 - Price Versions: Every Apply Is Saved

> **TL;DR** — Every price apply (REST or MCP, subscription or IAP) is now saved as a numbered
> version of its product: the full config that produced the prices (`source_config`, e.g. the
> GDP-bracket tiers and special list), every territory sent to Apple with its previous price,
> and Apple's result. Read them with `pricing_list_price_versions` / `pricing_get_price_version`;
> re-apply a version by passing its `items` to the apply tool. Pass `source_config` on every apply.

**Prerequisites**: [017 - IAP lifecycle and price schedules](017-iap-lifecycle-and-price-schedules.md)

## Why

An apply used to write nothing. `subscription_prices` / `iap_prices` hold only the current state, and
only after a sync, and the config typed into the pricing UI (tier prices, thresholds, overrides, special
territories) lived in React state. Refresher's Lifetime IAP was priced that way; when two special-tier
territories turned out to be on the wrong price, there was no record of the config that put them there.

## Model

`price_versions` (`backend/app/models/price_version.py`, migration `c3a9e1f4b7d2`), one row per apply or
baseline, numbered per `(app_id, product_kind, product_ref_id)`:

| Column | Holds |
|---|---|
| `version` | 1, 2, 3 … per product (unique; a lost race retries with the next number) |
| `source` | `api` (REST/UI), `mcp`, or `baseline` |
| `config` | the `PricePreviewRequest` sent as `source_config`, verbatim; `null` if the caller sent none |
| `base_territory_code` | IAP only: the base Apple actually received (after the fallback) |
| `intro_offer` | subscription free-trial config, if any |
| `items` | one entry per territory: price point, customer price, currency, previous price and price point, `force`, `origin` |
| `result` | the `PriceApplyResponse` |
| `note` | free text |

`origin` is `applied` (Apple accepted it), `skipped` (±50% band), `failed` (unknown price point or Apple
refused), `preserved` (IAP padding: Apple replaces the whole schedule, so untouched territories are resent),
or `current` (a baseline). For an IAP, one version is therefore the complete price list.

## Rules

- **`backend/app/services/pricing/versions.py` is the only writer.** The four apply paths call
  `record_apply_version`, which never turns a successful Apple change into an error: a failed save is
  rolled back and logged, and the apply result is still returned.
- **Pass `source_config` on every apply.** The UI sends the preview request that produced the prices
  (cleared whenever the preview is). MCP callers should pass the same `PricePreviewRequest` they previewed with.
- **Take a baseline before changing a product that has no version yet**: sync its prices, then
  `pricing_snapshot_price_version` (writes only to our DB).
- Reads (`pricing_list_price_versions`, `pricing_get_price_version`, `GET /apps/{id}/price-versions[/{vid}]`)
  are owner-scoped and read-only in the consent gate.
