"""MCP prompts — pre-built procedures the LLM can offer the user."""

from __future__ import annotations

from app.mcp.server import mcp


@mcp.prompt
def swap_product_safely(app_id: int, product_id: str, new_product_id: str) -> str:
    """Walk through swapping a subscription/IAP productId end-to-end."""
    return (
        f"You are helping the user swap product '{product_id}' on app {app_id} "
        f"to a new productId '{new_product_id}'. Steps:\n\n"
        "1. Call `pricing.list_subscriptions` (or `pricing.list_iaps`) to find "
        f"the local id for product '{product_id}'.\n"
        "2. Call the matching `swap.subscription_product` or `swap.iap` tool "
        f"with `new_product_id='{new_product_id}'`, `auto_archive=true`, "
        "`swap_revenuecat=true`. Confirm with the user before invoking.\n"
        "3. Read the `ios_checklist` field in the response and walk the user "
        "through it — that is the ground-truth list of what their iOS app "
        "must change.\n"
        "4. If `revenuecat_steps` shows failures, surface them clearly so the "
        "user can fix RC linkage manually.\n"
    )


@mcp.prompt
def optimize_keywords(app_id: int, locale: str = "en-US") -> str:
    """Run a structured keyword-optimization pass on an app."""
    return (
        f"Optimize the keyword strategy for app {app_id} in locale {locale}. "
        "Steps:\n\n"
        "1. Call `aso.aso_check` to see current metadata gaps.\n"
        "2. Call `metadata.get_snapshot` to read existing title/subtitle/keywords/description.\n"
        "3. Call `keywords.list_for_app` to see currently tracked keywords + rankings.\n"
        "4. Call `keywords.search` and `keywords.suggestions` to find new candidates.\n"
        "5. Call `clash.run` to compare against competitor keyword usage.\n"
        "6. Propose concrete edits as a list of `{field, current, proposed, rationale}` "
        "rows. Do NOT call `metadata.update_locale` until the user approves.\n"
    )
