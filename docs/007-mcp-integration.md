# 007 — MCP Integration

ASO-Light ships a Model Context Protocol (MCP) server mounted on the FastAPI
backend at `/mcp`. It exposes the project's full REST surface (~124 tools
across apps, pricing, metadata, keywords, reviews, visibility, RevenueCat,
ASO audit, swap, etc.) so an LLM client (Claude Desktop, OpenAI MCP client,
custom agents) can drive the product programmatically.

---

## What you can do with it

- "List my apps" / "show me the audit for app 7" / "what keywords are we tracking
  in en-US?"
- "Show me which user/PAT/ASC credentials this MCP client is actually using"
- "Show me current prices for the Pro subscription, then preview a 10% bump
  in EU territories"
- "Translate the German description draft and propose edits — but don't apply
  yet"
- "Swap `com.app.pro_monthly` for `com.app.pro_monthly.v2`, archive the old
  one, swap RevenueCat" — and get the iOS-side change checklist back inline
- "Check anomalies on my visibility watches" / "draft a reply to review 12345"

The full tool list is reachable from any MCP client; everything that the REST
API can do, the MCP server can do.

---

## Authentication: Personal Access Tokens (PATs)

Standard JWT access tokens expire in 30 minutes — too short for a long-lived
client config. PATs are long-lived bearer tokens scoped to a single user.

### Lifecycle

| Operation | Endpoint                              | Notes                                  |
| --------- | ------------------------------------- | -------------------------------------- |
| Issue     | `POST /api/v1/auth/tokens`            | Returns plaintext **once**             |
| List      | `GET /api/v1/auth/tokens`             | No plaintext — only `id`, `name`, `last_used_at` |
| Revoke    | `DELETE /api/v1/auth/tokens/{id}`     | Sets `revoked_at`; subsequent requests 401 |

Token format: `aso_pat_<32-byte-base64url>`. Stored as sha256 hash; constant-time
compare on auth. The plaintext is shown once at creation and never again — copy
it into your MCP client config immediately.

### Issuing one via curl

```bash
# 1. Login (existing JWT flow) to get a short-lived access token
curl -s -X POST http://localhost:8002/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}' | jq -r .access_token

# 2. Issue a PAT
curl -s -X POST http://localhost:8002/api/v1/auth/tokens \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"claude-desktop"}' | jq

# Copy the .token field — you won't see it again.
```

Or use the **Settings → Personal Access Tokens** panel in the web UI.

---

## Client config

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aso-light": {
      "url": "http://localhost:8002/mcp/",
      "headers": {
        "Authorization": "Bearer aso_pat_xxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

Restart Claude Desktop. The tool list should populate (~124 tools).

### Other MCP clients (OpenAI Agents SDK, custom)

Any client that speaks streamable-HTTP MCP works. Point it at `http://<host>/mcp/`
(trailing slash matters — `redirect_slashes=False` is the project convention)
with `Authorization: Bearer <PAT>`. Bare `/mcp` 307-redirects to `/mcp/`, so
clients that send POST/DELETE on the bare path still work.

### Behind a reverse proxy

The MCP transport is plain HTTP (streamable). Pass `Authorization` through
to the backend; no special websocket handling required. CORS is already
permissive on the existing FastAPI app.

---

## Tool reference

Tools are namespaced `<module>.<action>`:

| Module        | Tools | Domain                                                           |
| ------------- | ----- | ---------------------------------------------------------------- |
| `account`     | 1     | Show the authenticated PAT, user, owned ASC credentials, and visible apps |
| `apps`        | 3     | List/get/sync apps                                               |
| `aso`         | 1     | Run the ASO audit checklist on an app                            |
| `availability`| 2     | Get / update the app's available territories                     |
| `clash`       | 1     | Side-by-side competitor comparison                               |
| `indices`     | 3     | GDP / PPP / BigMac / Spotify / Netflix index status & refresh    |
| `keywords`    | 13    | iTunes search/suggestions, keyword tracking, rankings, competitors |
| `keyword_intel`| 2    | Cached keyword volume/difficulty: read the cache, refresh the providers |
| `metadata`    | 10    | App-info / version metadata, locale CRUD, bulk apply, AI translate |
| `presets`     | 5     | Pricing-formula preset CRUD                                      |
| `pricing`     | 44    | Subscription/IAP/group/intro-offer/price CRUD + sync + apply + price points + bulk export/import |
| `revenuecat`  | 23    | RC credential, products, entitlements, offerings, packages       |
| `reviews`     | 7     | List, draft reply, translate, post/edit/delete responses         |
| `screenshots` | 4     | Main product page: per locale × display-type counts + gap worklist (`screenshots_list`), idempotent positional upload (`screenshots_upload`), delete + empty-set prune (`screenshots_delete`), CPP-vs-default montage (`screenshots_compare`) |
| `swap`        | 3     | Swap subscription / IAP productId end-to-end + suggest new id    |
| `territories` | 1     | List App Store territories with currency / GDP / VAT             |
| `visibility`  | 7     | Watches, snapshots, anomalies, share-of-voice                    |

Get the live, authoritative list at session start from your MCP client. Each
tool ships with the same Pydantic schema the REST API uses, so arguments and
return types are self-describing.

### High-leverage workflows

**Swap a subscription productId safely** — the showcase
1. `pricing_list_subscription_groups(app_id)` to find the local subscription id
2. `swap_suggest_new_product_id(current_product_id)` to get a sane new id
3. `swap_subscription_product(app_id, subscription_id, new_product_id, auto_archive=true, swap_revenuecat=true)`
4. Read the returned `ios_checklist` — it tells the operator exactly what
   their iOS app must change. Full guidance:
   [006-product-swap-ios-integration.md](006-product-swap-ios-integration.md).

**Optimize keywords for a locale**
1. `aso_aso_check(app_id)` → see metadata gaps
2. `metadata_get_snapshot(app_id)` → read current title/subtitle/keywords
3. `keywords_list_for_app(app_id)` → see currently tracked keywords + ranks
4. `keyword_intel_list(app_id)` → cached volume/difficulty per keyword
5. `keywords_search` / `keywords_suggestions` → find new candidates
6. `clash_run(app_id)` → compare against competitors
7. Propose edits, then `metadata_update_locale(...)` after user approval

Two different refreshes, easy to mix up:
`keywords_refresh_rankings(app_id)` re-scrapes iTunes SERP ranks for tracked
keywords, while `keyword_intel_refresh_providers(app_id)` re-runs the
volume/difficulty providers into `keyword_intel_cache`
([010](010-keyword-intelligence.md)).


**Bulk price update**
1. `pricing_export_prices(app_id)` → CSV/Excel of current prices
2. (Edit offline)
3. `pricing_import_prices(app_id, file_base64)` → preview the import
4. `pricing_preview_subscription_prices(app_id, sub_id, ...)` → see the diff
5. `pricing_apply_subscription_prices(app_id, sub_id, ...)` → push to ASC

**Repair an interrupted localized screenshot run**

ASC's post-upload polling is flaky, so a 40-locale bulk upload can finish
looking complete while some locales are silently short — and Apple only says so
at submit time. The list tool is the API-side count that finds them:

1. `screenshots_list(app_id)` → per locale × display-type counts. `gaps` is the
   repair worklist (`locale`, `display_type`, `count`, `expected`, `missing`);
   `expected` defaults to the highest count any locale reached, so the locales
   that finished define the target. Pin it explicitly with `expected_count`.
2. `screenshots_upload(app_id, locale, display_type, file_base64, file_name,
   position=…)` per gap. Idempotent per (locale, display type, position): a
   re-run replaces the slot instead of doubling the locale. `verified` is the
   read-back verdict — `false` means Apple has not reported `COMPLETE` yet.
3. `screenshots_delete(app_id, locale, display_type, position=… | screenshot_id=…
   | delete_all=true)` to drop a wrong asset or clear a whole device family.
   Emptying a set prunes it, so no orphan (configured but empty) set is left.
4. `screenshots_list(app_id)` again → `complete: true`.

All three resolve the app's **editable** App Store version; against a live or
locked version they fail with a message naming the state
([spec 010](specs/010-mcp-main-listing-screenshots.md)).

There are pre-built MCP prompts (`swap_product_safely`, `optimize_keywords`)
that walk through these flows. Most LLM clients show prompts as quick-pick
templates.

---

## Authorization model

Every tool that takes an `app_id` runs the same ownership chain that the
REST API enforces:

```
PAT → user_id → app.credential_id → credential.user_id == user_id
```

A PAT cannot access another user's apps. Implementation:
[`backend/app/mcp/context.py`](../backend/app/mcp/context.py) → `resolve_app`
delegates to [`_get_verified_app`](../backend/app/api/v1/_deps.py).

There are no scopes (yet). A PAT has full parity with the user's REST
permissions, and writes go through the same App Store Connect / RevenueCat
client paths the REST API uses. **Treat PATs as you would the user's
password** — they grant write access to live App Store metadata, prices,
and review responses.

---

## Operational notes

- **Mounted at** `/mcp` (not `/api/v1/mcp`) — the FastAPI app composes its
  lifespan with `mcp_app.lifespan` so the MCP session manager initializes on
  startup.
- **Long-running ASC operations** (clone/swap, sync_price_points, refresh
  rankings) run synchronously in-tool; expect tool calls to take tens of
  seconds for large operations. The MCP client's request-timeout settings
  apply.
- **Logs**: every tool invocation is logged through the same `app` logger
  the REST API uses. Look for `app.mcp.*` in dev logs.
- **Rate limiting**: the underlying ASCClient has a 150ms min interval
  between requests + 429 backoff. The MCP server inherits this transparently.
- **Anthropic translation cap**: `metadata_translate` is rate-limited per app
  (500 calls / rolling 30 days, persisted in `metadata_translation_cache`).
  The MCP tool returns the same quota-exceeded error that the REST endpoint
  returns.

---

## Adding a new tool

1. Add the corresponding REST endpoint first (or pick an existing one).
2. In the matching `backend/app/mcp/tools/<module>.py`, add a function with
   `@mcp.tool(name="<module>_<action>")`. **Use underscores, not dots** — the
   Anthropic tool-name regex is `^[a-zA-Z0-9_-]{1,64}$`, so a dotted name
   (`module.action`) is rejected by Claude Desktop. Claude Code silently
   rewrites `.`→`_`, which hides the bug until someone connects via Desktop.
3. Use `session_scope()` for the DB session, `resolve_app()` for app-scoped
   tools, `get_user_id()` for user-scoped tools.
4. Reuse Pydantic schemas from `backend/app/schemas/`.
5. Convert `HTTPException` from underlying helpers to
   `fastmcp.exceptions.ToolError`.
6. Verify with:
   ```bash
   cd backend && uv run python -c "
   import asyncio
   from app.mcp.server import mcp
   async def main():
       tools = await mcp.list_tools()
       print([t.name for t in tools if t.name.startswith('<module>.')])
   asyncio.run(main())
   "
   ```

---

## Troubleshooting

**"401 Unauthorized" from the MCP client.** Check the PAT is not revoked
(`GET /api/v1/auth/tokens`). Confirm the `Authorization: Bearer aso_pat_...`
header is being sent — some clients lowercase or strip headers.

**Tool list is empty in the client.** Confirm `http://localhost:8002/mcp/`
returns 401 on `GET` (auth-required, mount works). 404 means the mount didn't
register; 200 with no body usually means a stale Vite/CORS proxy in front.

**A tool returns "Stored credential is not a valid PEM private key."** The
encrypted .p8 stored on the credential is corrupt or was uploaded as a fixture.
Delete the credential in **Settings → ASC Credentials** and re-upload your real
.p8 file. The validator now rejects non-parseable PEMs at upload time.

**A tool returns "not found" for an app you own.** The user_id resolution
goes through the PAT → owner chain. If you issued the PAT under a different
account than the one that owns the app, you'll get `App access denied`. Re-issue
the PAT under the right account. `account_whoami` is the fastest way to confirm
which user, PAT, credentials, and app rows the current MCP session can see.

**`metadata_translate` returns quota-exceeded.** 500 calls / 30 days per app.
Wait or raise the cap by editing the `MetadataTranslationCache` row.

**Swap tool succeeded but RC steps failed.** The ASC side is done; only RC
re-pointing is missing. Fix RC manually (or re-run via
`pricing` tools / RC dashboard), then iOS can proceed per the response's
`ios_checklist`. See [006](006-product-swap-ios-integration.md) §8.
