---
status: current
created: 2026-09-22
updated: 2026-09-22
---

# 020 - Documentation Audit (2026-09-22)

> **TL;DR** — First audit of this tree: 41 docs → 33. Two long-done specs archived, five
> executed plan artifacts and one stale app-specific draft deleted, and the whole
> `docs/superpowers/` ASA design folded into 011. Every surviving doc now has a header and
> a TL;DR. Three judgment calls below need you.

## Tree overview

33 docs, four groups:

**Foundations (`000-*`)** — architecture, changelog, tasks. The map, the history, the queue.

**Feature reference (`001`–`017`)** — one doc per subsystem, each naming its prerequisites:

| Area | Docs |
|------|------|
| Pricing | [001](001-pricing-system.md) engine · [017](017-iap-lifecycle-and-price-schedules.md) IAP lifecycle + price schedules |
| ASC platform | [002](002-asc-integration.md) API client · [004](004-localization-management.md) product localizations · [005](005-subscription-management.md) subscriptions |
| Listing & metadata | [006](006-metadata-editor.md) metadata editor · [013](013-custom-product-pages-and-visual-compare.md) custom product pages · [015](015-product-page-optimization.md) A/B experiments |
| Keywords & paid | [003](003-keyword-analysis.md) tracker · [010](010-keyword-intelligence.md) volume/difficulty · [011](011-apple-search-ads-analytics.md) ASA analytics · [016](016-apple-ads-platform-api-research.md) successor API research |
| Reviews | [009](009-reviews-theme-classifier.md) classifier + queue · [014](014-reviews-module-security-findings.md) security findings |
| Cross-cutting | [007](007-mcp-integration.md) MCP server · [012](012-growth-recommendations.md) growth advisor · [006-product-swap](006-product-swap-ios-integration.md) iOS swap guidance |

**Work queue (`specs/`, `bugs/`)** — 7 specs (2 draft, 1 superseded, 4 done awaiting the
30-day archive window) and 4 bugs (1 fixed, 3 open, all in the reviews module).

**`archive/`** — retired work items. Never audited again.

## Since the last audit

No previous audit exists; this is the baseline. The tree grew organically to 41 docs with
**no doc carrying the mandatory `status`/`created`/`updated` header** except the one written
today, and none carrying a TL;DR. Both are now repaired tree-wide:

- **headers filled: 22 docs** — `created`/`updated` recovered from git history (`--follow`),
  `status` inferred from location.
- **tl;drs written: 31 docs** — each authored from the doc's own body, not templated.

Topics that grew since the tree started: pricing (now two docs), reviews (a feature doc, a
findings doc and four bug docs), and the MCP surface (179 → 187 tools this week). Areas that
went quiet: nothing was abandoned — the deletions below are all *executed* working material,
not dropped features.

## Removed

| Doc | Class | Why |
|-----|-------|-----|
| `specs/006-subscription-management.md` | archived | `done`, untouched 145 days |
| `specs/007-metadata-editor-and-cross-loc.md` | archived | `done`, untouched 140 days |
| `008-refresher-asc-metadata-recommendations.md` | deleted | Dead orphan: copy recommendations for one app off a 2026-05-08 snapshot, zero inbound links, never in `INDEX.md`. Its inputs ("3 tracked keywords", `READY_FOR_SALE`) are 137 days stale and superseded by the metadata editor and keyword intel tooling |
| `plans/bug-001.md` | deleted | Dead orphan: executed plan for a fixed bug |
| `plans/spec-004.md` | deleted | Dead orphan. Its one durable output — *why* spec 004 was stopped — is already recorded in the spec's own supersession note, so nothing was lost |
| `plans/spec-005.md` | deleted | Dead orphan: executed plan for a done spec |
| `plans/spec-008.md` | deleted | Dead orphan: executed plan for a done spec |
| `superpowers/specs/2026-05-08-…-design.md` | merged away | See below |
| `superpowers/plans/2026-05-08-…-analytics.md` | merged away | See below |

Archiving the two specs broke three inbound links (`005`, `006-metadata-editor`,
`specs/012`); all were repointed to `archive/specs/`, and the two `INDEX.md` rows dropped.
`docs/plans/` and `docs/superpowers/` are now empty and gone.

## Consolidated

**Apple Search Ads cluster → [011 - Apple Search Ads Analytics](011-apple-search-ads-analytics.md)**

`docs/superpowers/` held a 431-line design doc and a 2,628-line implementation plan for ASA
analytics, both dated 2026-05-08, both with zero inbound links. The work shipped; doc 011
already covers all 13 design sections (auth, data model, sync flow, MCP surface, UI, error
handling, authorization, testing, constraints) against the code as built, so the design doc
had become a second, staler description of the same system.

Folded into 011 before removal: the design's three **open questions**, which were still
genuinely unresolved and would otherwise have been lost — `archived_at` retention policy, a
possible partial index on `asa_metric_daily`, and exposing `asa_sync_operation` over MCP.
They now live in a "Follow-ups" section. That last one has gained force since it was written:
[017](017-iap-lifecycle-and-price-schedules.md) added `clone_list_operations` /
`clone_get_operation` for exactly this reason — an agent that starts a long operation needs a
way to read its status — and ASA sync has the same shape with no such tool.

Nothing else was unique to the removed files: the plan was a checkbox list of work already
done, and "files to create" is now just the file tree.

## Open judgment calls

**1. `006` is used twice.** `006-metadata-editor.md` and `006-product-swap-ios-integration.md`
share a number, so `INDEX.md` lists two "006" rows and cross-references are ambiguous in
prose. *Recommendation:* renumber the product-swap doc to `018`, since `006-metadata-editor`
is the one other docs cite as a prerequisite. Not done here — it rewrites links in
`CLAUDE.md`, `INDEX.md`, `015` and the swap MCP tool's docstring, which is a code change.

**2. `specs/009` is misnamed.** The file is `009-asa-analytics.md` but the spec inside is
titled "Keyword Visibility Tracker" and explicitly is *not* Apple Search Ads — it is organic
SERP polling. Meanwhile `011-apple-search-ads-analytics.md` is the real ASA doc. Anyone
grepping for ASA work finds the draft that was never built. *Recommendation:* rename the
spec file to `009-keyword-visibility-tracker.md`.

**3. `specs/011` prose is stale.** It states "All 44 `pricing_*` MCP tools"; there are now
48. *Recommendation:* fix in place — but specs are a `/go`-owned work queue, so this audit
did not edit the body. Low urgency: the spec is deliberately `draft` and the number is
context, not a requirement.

Also noted, no action needed: `bugs/001-reviews-cross-app-idor.md` is `fixed` at 27 days and
becomes archivable on 2026-09-25. Bugs 002–004 are `open` and stay in the queue.

## Convention drift

The every-10th audit slot at `010-` was taken by a feature doc
([010 - Keyword Intelligence](010-keyword-intelligence.md)), so this audit is numbered `020`.
`INDEX.md` previously advertised a planned `010-audit.md` that could never exist. Keep `030`
free for the next one.
