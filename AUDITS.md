| Service | Path | Last audited commit | Audited on | Deps | Findings |
|---------|------|---------------------|------------|------|----------|
| backend | backend | working tree | 2026-08-27 | fastmcp 3.2.4 | Destructive-operation audit — see below |
| frontend | frontend | — | — | — | — |
| (root)  | .    | — | — | — | — |
| (docs)  | docs | — | 2026-09-22 | — | 33 docs @ 2026-09-22 — audit [020](docs/020-documentation-audit.md) |

## 2026-08-27 — Destructive MCP operations

**Scope.** All 173 registered MCP tools plus the REST routers, asking: what can
run against a live, published App Store listing without the user being asked?

**Finding: there was no consent gate of any kind.** No middleware, no confirm
parameter, no soft-delete, no undo, no feature flag. `dry_run` appeared zero
times in the repo. None of the 173 tools carried an MCP `destructiveHint`, so no
client could prompt on one. PATs are minted with `scopes=[]`
(`app/mcp/auth.py`) — every token is omnipotent. The two "ask the user first"
notes in `app/mcp/prompts.py` are prose an agent can ignore; nothing read them at
call time. `metadata_bulk_preview` did not gate `metadata_bulk_apply` — apply
recomputes its own plan and could be called cold.

**Resolution.** `app/mcp/consent.py` — one `ConsentGate` middleware registered at
`app/mcp/server.py`. A tool in `DESTRUCTIVE` (35 of 173) refuses its first call
and returns an impact statement plus a single-use token bound to that exact tool,
those exact arguments, and that user. Consent is per operation: there is no
session unlock, and a repeat of an approved call needs a fresh token. The same
middleware stamps `destructiveHint` on `tools/list`, which is what makes MCP
clients prompt a human.

Highest-severity items found (all now gated):

| tool | impact |
|---|---|
| `screenshots_delete` | wipes a device family's screenshots on the live version; Apple does not return the binaries. **MCP-only — no REST route, no UI** |
| `metadata_delete_locale` | delists an App Store language, discarding its copy locally and at Apple |
| `metadata_bulk_apply` | overwrites listing copy across arbitrary locales, no prior snapshot |
| `swap_subscription_product` / `swap_iap` | archived a live revenue product and rewired RevenueCat **by default** — defaults now `False` |
| `cpp_delete` | deletes a CPP whose id is wired into live Search Ads campaigns. **MCP-only** |
| `reviews_respond` | publishes public text under the app's name on the App Store |
| `asa_delete_credential` | cascades away all historical Search Ads metrics; past dates cannot be re-synced |
| `visibility_delete_watch` | watch plus its entire share-of-voice time series |

**Also fixed.**
- `app/mcp/tools/swap.py` — `auto_archive` and `swap_revenuecat` defaults `True` → `False`.
- `app/mcp/tools/screenshots.py` — `prune_empty_set` default `True` → `False`.
- `app/api/v1/credentials.py` — `DELETE /credentials/{id}` cascaded away every bound
  `App` and all its keyword history with no confirmation. Now requires
  `?confirm_app_count=N` matching the real count. REST-only, so no MCP tool reaches it.
- `app/services/asc/apps.py` — syncing an app under a second credential inserted a
  duplicate `App` row instead of rebinding, forking keyword tracking and IAP cache
  across the two. Now matches on App Store identity across the user's credentials
  and rebinds.

**Known gaps, deliberately deferred.**
- The impact statement is a static per-tool sentence plus the echoed arguments, not
  live cascade counts ("this deletes 4 apps, 791 keyword trackings"). Counts need a
  bespoke query per tool inside middleware; the safety property does not depend on
  them. See the `ponytail:` note in `app/mcp/consent.py`.
- No unique constraint on `apps (credential_id, asc_app_id)`. The rebinding fix
  handles the real case; the constraint would only guard a concurrent-sync race, and
  the migration would need a dedupe step for installs that already hold duplicates.
- `preview` → `apply` is still not linked. `metadata_bulk_apply` is gated by consent,
  but consent does not verify a preview was run first.
- A typed confirmation stops accidents and blind sweeps, not an agent that has decided
  to proceed — it can echo a token back. `destructiveHint` is the layer that puts the
  decision in front of a person.

**Tests.** `backend/tests/test_consent.py` (14) and
`backend/tests/test_apps_sync_rebinding.py` (2). The tripwire
`test_no_destructive_shaped_tool_escapes_the_gate` fails the build if a new
delete/archive/detach/apply tool ships without being registered in `DESTRUCTIVE`
or added to `REVIEWED_SAFE` with a reason.
