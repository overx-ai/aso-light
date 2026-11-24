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

> **TL;DR** — A daily job will pick 3 topics from Apple's category charts, run the niche finder and send the
> 5 best new ideas to Telegram, plus a Monday top 10. Each idea is a Markdown card Studio can approve or
> reject, and agents read them with MCP `idea_digest_list`.

**Prerequisites**: [020 - Niche finder](020-niche-finder.md)

## Decisions (owner, 2026-11-24)

Daily scan plus Monday top 10; topics automatic across categories; Telegram via Hero; exposed in MCP and to Studio.

## Design

1. **Topics.** Rotate through App Store categories (games excluded), 3 a day. A topic is the 2-word phrase most
   shared by the category's top-100 titles, across at least 2 developers, not used in the last 60 days.
2. **Ideas.** `NicheFinder.scan`; keep `opportunity >= IDEA_MIN_OPPORTUNITY`; a term whose core words were ever
   shown is not shown again; best 5.
3. **Cards.** `ideas/<date>-<slug>.md` with frontmatter, gitignored: `status: new`, rewritten by Studio on a decision.
4. **Message.** POST `{"text"}` to `IDEA_DIGEST_NOTIFY_URL`. A failed send is kept and sent with the next run.
   Monday adds the week's top 10, rejected ideas excluded.
5. **MCP.** `idea_digest_list(days, status)`, read-only. Ideas come from public data and are not per user.
6. **Schedule.** launchd `ai.overx.aso-ideas`; one scan a day, a second run the same day only retries delivery.

## Out of scope

Web page, per-user digests, storefronts other than US, AI-written pitches.

## Tasks
| ID | Description | Agent | Depends On | Status | Files |
|----|-------------|-------|------------|--------|-------|
| T1 | Digest service and runner | dev | — | pending | backend/app/services/idea_digest.py, backend/scripts/idea_digest.py, backend/app/core/config.py |
| T2 | MCP tool | dev | T1 | pending | backend/app/mcp/tools/ideas.py, backend/app/mcp/server.py, backend/app/mcp/consent.py |
| T3 | Studio manifest, launchd job | ops | T1 | pending | studio.json, ops/ai.overx.aso-ideas.plist, .gitignore |
| T4 | Tests | test-writer | T1 | pending | backend/tests/test_idea_digest.py |

## Acceptance

- No idea repeats across days; a same-day rerun does not rescan; a failed send reaches the next message.
- Monday lists at most 10 ideas from 7 days and skips rejected ones.
- Live: one real digest arrives in Telegram, its cards show in Studio, and `list_materials` returns them.
