---
status: current
created: 2026-10-17
updated: 2026-10-17
---

# PINS - Consumed Cross-Repo Contracts

> **TL;DR** — The contracts aso-light consumes from sibling repos, each pinned to the
> producer version it was built against. `/go` checks every row against the producer
> before it starts. Nothing to do unless a row goes stale.

| Contract | Producer repo | Pinned | Source of truth (path in producer repo) |
|----------|---------------|--------|------------------------------------------|
| app preview export layout `appstore-exports/previews/<project_id>/<locale>/01_preview.mp4` (pass the project folder, hero's `export_dir`, as `dir`) (+ optional `01_preview.poster.txt`), read by `previews_sync` | hero-ai | spec 069 | docs/specs/069-app-store-preview-export.md (Requirement 6, Cross-Repo Interfaces) |
