---
id: 021
title: "App idea digest: daily niche scan, Telegram, Studio cards, MCP"
status: in-progress
priority: p1
created: 2026-11-24
updated: 2026-11-24
repo: aso-light
release:
story:
depends_on: [020]
conflicts_with: []
---

# 021 - App Idea Digest

> **TL;DR** — Built: a daily job picks 3 topics from Apple's category charts, scans them and sends the 5 best new
> ideas to Telegram, plus a Monday top 10; ideas are Studio cards, and MCP `idea_digest_list` reads them.
> Open: the first live digest, blocked on 2026-11-24 by Apple's 403 on `/search`.

**Prerequisites**: [020 - Niche finder](020-niche-finder.md)

## Decisions (owner, 2026-11-24)

Daily scan plus Monday top 10; topics automatic across categories; Telegram via Hero; exposed in MCP and to Studio.

## Design

1. **Topics.** Rotate through App Store categories (games excluded), 3 a day, one per category. A topic is the
   2-3 word phrase most shared by the category's top-100 titles, across at least 2 developers, not used in the last
   60 days. A fragment that only occurs inside a longer shared phrase gives way to that phrase.
2. **Ideas.** One `NicheFinder.scan` per topic; keep `opportunity >= IDEA_MIN_OPPORTUNITY`, at most 2 real apps and
   median ratings `<= IDEA_MAX_MEDIAN_RATINGS`; at most 2 per topic, best 5. A term whose core words were ever shown
   is not shown again. Title-like hints (title punctuation, "by <maker>", company suffix) never take a scan slot.
3. **Apple limits.** Searches are paced 6 s apart; 5 empty answers in a row stop the run's searches, because bursts
   bring an IP-wide 403. Empty lookups are counted in the message. A topic whose lookups were all refused is not used
   up; when no topic got through (or charts or hints are down), the day is not used up either and a rerun rescans.
4. **Cards.** `ideas/<date>-<slug>.md` with frontmatter, gitignored: `status: new`, rewritten by Studio on a decision.
   Third-party text is stripped of control characters; an existing card is never overwritten.
5. **Message.** POST `{"text"}` to `IDEA_DIGEST_NOTIFY_URL`. Failed sends are queued (last 7) and sent one by one
   with the next run. Ideas are read back from cards newer than the last run, so a crash loses none. Monday adds the
   week's top 10, rejected ideas excluded.
6. **MCP.** `idea_digest_list(days, status)`, read-only. Ideas come from public data and are not per user.
7. **Schedule.** launchd `ai.overx.aso-ideas`; one scan a day, a second run the same day only retries delivery.
   A lock refuses a concurrent run; a corrupt `state.json` is set aside and started fresh.

## Out of scope

Web page, per-user digests, storefronts other than US, AI-written pitches.

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Digest service and runner | dev | — | done | backend/app/services/idea_digest.py, backend/scripts/idea_digest.py, backend/app/core/config.py |
| T2 | MCP tool | dev | T1 | done | backend/app/mcp/tools/ideas.py, backend/app/mcp/server.py, backend/app/mcp/consent.py |
| T3 | Studio manifest, launchd job | ops | T1 | done | studio.json, ops/ai.overx.aso-ideas.plist, .gitignore |
| T4 | Tests | test-writer | T1 | done | backend/tests/test_idea_digest.py |

## Acceptance

- No idea repeats across days; a same-day rerun does not rescan; a failed send reaches the next message.
- Monday lists at most 10 ideas from 7 days and skips rejected ones.
- Live: one real digest arrives in Telegram, its cards show in Studio, and `list_materials` returns them.
