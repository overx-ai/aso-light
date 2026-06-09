# 008 — Refresher ASC Metadata Recommendations

Draft metadata refresh for **Refresher: Breathing & Focus** based on the
ASO-Light snapshot synced on **2026-05-08**.

## Goal

Improve the highest-leverage App Store Connect fields we can update from the
metadata already in place, without rewriting the full product page.

## Constraints

- Current app state in ASO-Light: `READY_FOR_SALE`
- Currently editable fields in ASC snapshot:
  - `name`
  - `subtitle`
  - `privacy_policy_url`
  - `promotional_text`
- App name limit: 30 characters
- Subtitle limit: 30 characters
- Promotional text limit: 170 characters
- Current keyword coverage is too thin to justify a rewrite yet:
  - 3 tracked keywords total
  - only `wim hof` has a non-null rank in the local cache

## Recommendation

1. Update **Promotional Text** first in all 5 locales.
2. Pick one **subtitle direction** and align all locales around it.
3. Leave **app name**, **description**, and **keywords** unchanged for now.
4. Add localized **What’s New** text on the next versioned metadata pass.

## Promotional Text Drafts

Recommended copy is designed to shift the message away from generic polish
(`premium design`, `advanced stats`) and toward clearer user outcomes:
sleep, calm, anxiety relief, and guided habit-building.

| Locale | Chars | Draft |
| --- | ---: | --- |
| `en-US` | 153 | `Sleep faster, reduce anxiety, and build a daily breathing habit with guided Box, 4-7-8, Coherent, and Wim Hof sessions plus Apple Watch heart-rate stats.` |
| `de-DE` | 136 | `Schneller einschlafen, Stress senken und mit geführten Box-, 4-7-8-, kohärenten und Wim-Hof-Sitzungen eine tägliche Atempraxis aufbauen.` |
| `es-ES` | 118 | `Duerme antes, reduce la ansiedad y crea tu hábito con sesiones guiadas de Box, 4-7-8, respiración coherente y Wim Hof.` |
| `pt-BR` | 121 | `Durma mais rápido, reduza a ansiedade e crie seu hábito com sessões guiadas de Box, 4-7-8, respiração coerente e Wim Hof.` |
| `ru` | 124 | `Засыпайте быстрее, снижайте тревогу и стройте привычку с управляемыми сессиями Бокс, 4-7-8, когерентного дыхания и Вим Хофа.` |

## Subtitle Variants

### `en-US`

- Recommended A — `Breathwork for Sleep & Calm` — 27
- Option B — `Sleep, Calm & 4-7-8 Breathing` — 29
- Option C — `Box, 4-7-8 & Wim Hof Breath` — 27

### `de-DE`

- Recommended A — `Atemübungen für Schlaf & Ruhe` — 29
- Option B — `Schlaf, Ruhe & 4-7-8-Atmung` — 27
- Option C — `Box, 4-7-8 & Wim-Hof-Atmung` — 27

### `es-ES`

- Recommended A — `Respira para dormir en calma` — 28
- Option B — `Sueño, calma y 4-7-8` — 20
- Option C — `Box, 4-7-8 y Wim Hof` — 20

### `pt-BR`

- Recommended A — `Respire para dormir e relaxar` — 29
- Option B — `Sono, calma e respiração 4-7-8` — 30
- Option C — `Box, 4-7-8 e Wim Hof` — 20

### `ru`

- Recommended A — `Дыхание для сна и спокойствия` — 29
- Option B — `Сон, спокойствие и 4-7-8` — 24
- Option C — `Бокс, 4-7-8 и Вим Хоф` — 21

## Why These Changes

- Current promotional text is polished but generic. It undersells the app’s
  strongest practical outcomes.
- The English subtitle currently communicates a stronger use case than the
  other locales. These drafts make the positioning more consistent.
- Keywords are already close to the ASC limit, but we do not yet have enough
  ranking evidence to justify replacing them confidently.
- The long descriptions are acceptable for now; they are not the fastest win.

## What To Leave Alone For Now

- App name in all locales
- Privacy Policy URL
- Marketing URL
- Support URL
- Version description copy
- Keyword lists, until tracking coverage improves

## Next Pass

When ready for the next metadata iteration:

1. Add localized `What’s New` text for the next release.
2. Expand tracked keywords beyond `breathing`, `coherent`, and `wim hof`.
3. Revisit keyword lists after real ranking data accumulates.
4. Only then consider tightening the long description openings.

## Related

- [006 - Metadata Editor + Cross-Loc](006-metadata-editor.md)
- [007 - MCP Integration](007-mcp-integration.md)
- Apple App information: https://developer.apple.com/help/app-store-connect/reference/app-information/
- Apple Platform version information: https://developer.apple.com/help/app-store-connect/reference/platform-version-information
