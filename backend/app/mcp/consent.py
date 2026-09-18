"""Per-operation consent gate for destructive MCP tools.

aso-light drives a real, published App Store listing through 173 agent-callable
tools, several of which are irreversible: ``screenshots_delete`` wipes a device
family's screenshots and Apple does not hand the binaries back;
``metadata_delete_locale`` delists a language; ``swap_subscription_product``
archives a live revenue product. Nothing gated any of them.

This module adds one choke point. A tool in :data:`DESTRUCTIVE` refuses its first
call and returns an impact statement plus a single-use token; the caller must
repeat the call with that token to proceed.

**Consent is per operation, never a session unlock.** There is no "destructive
mode", no env flag that opens the door, and no window during which later calls
pass. Every destructive call mints and consumes its own token — including a
repeat of one approved seconds earlier. A token is bound to the exact tool, the
exact arguments and the user it was issued to, so it cannot be minted cheaply
once and replayed across a sweep of locales.

Modelled on :mod:`app.core.ratelimit`: process-global store, single-instance
deployment assumption, and :func:`reset_consent_state` as a test hook.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import ToolAnnotations

# The argument callers echo the token back in. Stripped before the tool runs, so
# tools need no `confirm` parameter of their own and their schemas stay clean.
CONFIRM_ARG = "confirm"

_TTL_SECONDS = 300.0
# Bounds the store if tokens are minted and never used. Well above any real
# burst; the prune-on-mint below does the routine cleanup.
_MAX_PENDING = 512


# Tool -> what it destroys, in the caller's words. Membership in this mapping IS
# the gate: a destructive tool absent from here is ungated, which is what
# ``test_consent.py::test_destructive_tools_are_registered`` exists to catch.
#
# Deliberately NOT listed: ``presets_delete`` (local, trivially recreated),
# ``screenshots_upload`` (a normal publish step — gating it would mean ~60
# confirmations for one store refresh; its in-place replace is bounded by the
# set it targets), and ``swap_suggest_new_product_id`` (returns advice, writes
# nothing).
DESTRUCTIVE: dict[str, str] = {
    # --- irreversible, hits the LIVE published listing -------------------
    "screenshots_delete": (
        "Permanently deletes screenshots from the live App Store version. "
        "With delete_all, the entire device family goes, and Apple does not "
        "return the binaries — they must be re-uploaded from local originals."
    ),
    "metadata_delete_locale": (
        "Delists an entire App Store language and discards its copy, locally "
        "as well as at Apple. There is no remaining copy to restore from."
    ),
    "metadata_update_locale": (
        "Overwrites live listing copy for a locale. The previous value is not "
        "snapshotted anywhere before the write."
    ),
    "metadata_bulk_apply": (
        "Overwrites title/subtitle/keywords/description across every locale "
        "passed, in one call, with no snapshot of the prior values."
    ),
    "cpp_delete": (
        "Deletes a Custom Product Page. CPP ids are wired into live Apple "
        "Search Ads campaigns, so deleting one breaks the traffic pointing at "
        "it."
    ),
    "availability_update": (
        "Changes which territories the app is on sale in. Removing territories "
        "pulls it from those stores."
    ),
    "swap_subscription_product": (
        "Swaps a live subscription product; archives the old one at Apple "
        "(not reversible) and can re-point RevenueCat entitlements and "
        "offering packages. Directly revenue-affecting."
    ),
    "swap_iap": (
        "Swaps a live in-app purchase; archives the old one at Apple (not "
        "reversible) and can re-point RevenueCat. Directly revenue-affecting."
    ),
    "pricing_apply_subscription_prices": (
        "Writes new subscription prices to live products across every "
        "territory passed. Per-item force=True bypasses the ±50% safety band."
    ),
    "pricing_apply_iap_prices": (
        "Writes new IAP prices to live products across every territory "
        "passed. Per-item force=True bypasses the ±50% safety band."
    ),
    "pricing_bulk_sync_subscription_localizations": (
        "Bulk-overwrites live subscription store copy for every locale passed."
    ),
    "pricing_bulk_sync_iap_localizations": (
        "Bulk-overwrites live IAP store copy for every locale passed."
    ),
    "pricing_delete_subscription": (
        "Deletes a subscription at Apple and drops the local row with it."
    ),
    "pricing_delete_subscription_localization": (
        "Deletes a live subscription localization at Apple."
    ),
    "pricing_delete_subscription_group_localization": (
        "Deletes a live subscription group localization at Apple."
    ),
    "pricing_delete_subscription_intro_offer": (
        "Deletes a live introductory offer — affects what new subscribers pay."
    ),
    "reviews_respond": (
        "Publishes public text under your app's name on the App Store, visible "
        "to everyone browsing the listing."
    ),
    "reviews_update_response": (
        "Edits public text published under your app's name on the App Store."
    ),
    "reviews_delete_response": (
        "Removes a public response from the App Store and forgets the local "
        "mapping to the review it answered."
    ),
    "experiment_stop": (
        "Stops a running product page optimization test. Apple does not allow "
        "restarting it — the run and its accumulated data are finished."
    ),
    "experiment_submit_for_review": (
        "Submits an experiment to Apple's App Review. Externally visible and "
        "not silently undoable."
    ),
    "experiment_delete": "Deletes a product page optimization experiment.",
    "experiment_delete_treatment": "Deletes an experiment treatment.",
    # --- irreversible local history, not re-derivable --------------------
    "asa_delete_credential": (
        "Cascades away the Apple Search Ads org, its campaigns, ad groups, "
        "keywords AND all historical performance metrics. Past-date ASA "
        "history cannot be re-synced from Apple."
    ),
    "asa_remove_negative_keyword": (
        "Removes the negative keyword from live Apple Search Ads first, which "
        "immediately re-opens ad spend on that search term."
    ),
    "visibility_delete_watch": (
        "Deletes a share-of-voice watch and every historical snapshot under "
        "it. The time series cannot be reconstructed."
    ),
    "keywords_remove": (
        "Deletes a tracked keyword and its entire ranking history."
    ),
    "keywords_remove_competitor": (
        "Deletes a competitor and the keyword corpus harvested from it."
    ),
    "revenuecat_delete_credential": (
        "Deletes the stored RevenueCat secret key; it must be re-entered by "
        "hand to reconnect."
    ),
    "revenuecat_archive_product": (
        "Archives a RevenueCat product. Paying users lose access to it on "
        "their next getOfferings call."
    ),
    "revenuecat_delete_entitlement": (
        "Deletes a RevenueCat entitlement — paying users lose the access it "
        "granted."
    ),
    "revenuecat_delete_offering": (
        "Deletes a RevenueCat offering; clients requesting it stop receiving "
        "packages."
    ),
    "revenuecat_delete_package": "Deletes a package from a RevenueCat offering.",
    "revenuecat_detach_product_from_entitlement": (
        "Detaches a product from an entitlement — cuts off entitlement access "
        "for users who bought that product."
    ),
    "revenuecat_detach_products_from_package": (
        "Detaches products from a RevenueCat package."
    ),
}


# Tools that only READ. Published as ``readOnlyHint: True`` so a client can skip
# the approval prompt — Claude Code's plan mode prompts on any tool whose
# annotations are null, which made the whole server unusable for research.
#
# An ALLOWLIST, deliberately, not "everything not in DESTRUCTIVE". That set is a
# curated *high-risk* list, not the set of all writes: 64 tools mutate state
# without being in it (``cpp_create``, ``keywords_add``, ``apps_sync``,
# ``analytics_enroll``, ``indices_refresh``, ``visibility_poll_watch``…).
# Name-prefix rules fail the same way — ``visibility_*`` contains
# ``create_watch``/``delete_watch``/``poll_watch``.
#
# The direction matters: an allowlist fails CLOSED. A tool nobody classified is
# simply unannotated and keeps prompting. A heuristic would fail OPEN — a future
# ``pricing_bulk_fanout`` matches no write verb and would ship advertised as
# safe to call unattended against a live listing.
# ``test_consent.py::test_no_write_shaped_tool_is_marked_read_only`` is the
# mechanical tripwire for exactly that mistake.
READ_ONLY: frozenset[str] = frozenset({
    "account_whoami",
    # --- analytics (local fact-table reads) ---
    "analytics_cpp_performance",
    "analytics_downloads",
    "analytics_engagement",
    "analytics_status",
    # --- apps ---
    "apps_get",
    "apps_list",
    # --- Apple Search Ads (reports + suggestions; test_credential only probes) ---
    "asa_get_campaign",
    "asa_list_ad_groups",
    "asa_list_campaigns",
    "asa_list_cpp_ads",
    "asa_list_credentials",
    "asa_list_keywords",
    "asa_list_negative_keywords",
    "asa_list_orgs",
    "asa_paid_organic_join",
    "asa_performance_report",
    "asa_search_term_report",
    "asa_suggest_negative_candidates",
    "asa_suggest_organic_keywords_to_track",
    "asa_test_credential",
    "availability_get",
    # --- custom product pages ---
    "cpp_get",
    "cpp_list",
    "cpp_list_screenshots",
    # --- experiments (results are not exposed by Apple; these are config reads) ---
    "experiment_get",
    "experiment_list",
    "experiment_list_treatment_screenshots",
    "experiment_list_treatments",
    "indices_list_gdp",
    "indices_status",
    "keyword_intel_list",
    # --- keywords (search/suggestions proxy iTunes, but write nothing) ---
    "keywords_cross_localization",
    "keywords_get_rankings",
    "keywords_list_competitor_keywords",
    "keywords_list_competitors",
    "keywords_list_for_app",
    "keywords_search",
    "keywords_suggestions",
    # --- metadata (bulk_preview computes a diff; bulk_apply is the write) ---
    "metadata_bulk_preview",
    "metadata_get_locale",
    "metadata_get_snapshot",
    "metadata_keyword_coverage",
    "presets_get",
    "presets_list",
    # --- pricing (preview/resolve compute, they do not apply) ---
    "pricing_export_prices",
    "pricing_get_iap_prices",
    "pricing_get_iap_review_screenshot",
    "pricing_get_subscription_availability",
    "pricing_get_subscription_prices",
    "pricing_get_subscription_review_screenshot",
    "pricing_iap_price_points_status",
    "pricing_list_iap_localizations",
    "pricing_list_iaps",
    "pricing_list_subscription_group_localizations",
    "pricing_list_subscription_groups",
    "pricing_list_subscription_intro_offers",
    "pricing_list_subscription_localizations",
    "pricing_preview_iap_prices",
    "pricing_preview_subscription_prices",
    "pricing_resolve_iap_price",
    "pricing_resolve_subscription_price",
    "pricing_subscription_price_points_status",
    # --- RevenueCat ---
    "revenuecat_get_credential",
    "revenuecat_list_apps",
    "revenuecat_list_entitlements",
    "revenuecat_list_offerings",
    "revenuecat_list_packages",
    "revenuecat_list_products",
    "revenuecat_test_credential",
    "reviews_get",
    "reviews_list",
    "screenshots_compare",
    "screenshots_list",
    "territories_list",
    # --- visibility (create/delete/poll_watch are writes and are NOT here) ---
    "visibility_competitor_sites",
    "visibility_get_sov",
    "visibility_list_anomalies",
    "visibility_list_snapshots",
    "visibility_list_watches",
})


@dataclass(frozen=True)
class _Pending:
    tool: str
    fingerprint: str
    user_id: str
    expires_at: float


# token -> _Pending. Process-global, mirroring app.core.ratelimit._buckets.
_pending: dict[str, _Pending] = {}


def reset_consent_state() -> None:
    """Clear all outstanding consent tokens. Test-only hook."""
    _pending.clear()


def _user_id() -> str:
    """Identify the caller from the MCP access token.

    Deliberately reads the token directly rather than importing
    ``app.mcp.context`` — this module must stay free of app imports so it can be
    registered on the server before the tool modules load.
    """
    token = get_access_token()
    if token is None:
        return "anonymous"
    return str((token.claims or {}).get("user_id", "anonymous"))


def _fingerprint(tool: str, arguments: dict[str, Any]) -> str:
    """Hash the exact call being consented to.

    Canonical JSON, so key order cannot produce two fingerprints for one call.
    ``default=str`` keeps a stray non-serialisable argument from turning a
    safety check into a 500.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(f"{tool}\x00{canonical}".encode()).hexdigest()


def _prune(now: float) -> None:
    for token in [t for t, p in _pending.items() if p.expires_at <= now]:
        del _pending[token]
    if len(_pending) > _MAX_PENDING:
        for token in sorted(_pending, key=lambda t: _pending[t].expires_at)[
            : len(_pending) - _MAX_PENDING
        ]:
            del _pending[token]


def _mint(tool: str, fingerprint: str, user_id: str) -> str:
    now = time.monotonic()
    _prune(now)
    token = secrets.token_urlsafe(12)
    _pending[token] = _Pending(tool, fingerprint, user_id, now + _TTL_SECONDS)
    return token


def _challenge(tool: str, arguments: dict[str, Any], token: str) -> str:
    # ponytail: static risk sentence + echoed arguments, not live cascade counts
    # ("deletes 4 apps, 791 keyword trackings"). Real counts need a bespoke query
    # per tool inside middleware; the safety property does not depend on them.
    # Upgrade path: a per-tool `count_impact(args) -> str` callable in DESTRUCTIVE.
    shown = json.dumps(arguments, sort_keys=True, indent=2, default=str)
    return (
        f"CONSENT REQUIRED — {tool} is destructive and was not confirmed.\n\n"
        f"{DESTRUCTIVE[tool]}\n\n"
        f"Arguments this consent covers:\n{shown}\n\n"
        f"Show this to the user. If they approve, repeat the identical call "
        f'with {CONFIRM_ARG}="{token}".\n'
        f"The token is single-use, expires in {int(_TTL_SECONDS)}s, and covers "
        f"only these exact arguments — every destructive call needs its own."
    )


def _annotations_for(name: str) -> ToolAnnotations:
    """The risk hints a client sees for one tool. See :meth:`on_list_tools`."""
    if name in DESTRUCTIVE:
        return ToolAnnotations(
            destructiveHint=True, readOnlyHint=False, idempotentHint=False
        )
    if name in READ_ONLY:
        return ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    return ToolAnnotations(readOnlyHint=False, destructiveHint=False)


class ConsentGate(Middleware):
    """Refuse destructive tool calls that carry no matching consent token."""

    async def on_list_tools(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        """Stamp the protocol's risk hints on every tool.

        MCP clients read these to decide whether to prompt a human. Doing it
        here rather than on 179 decorators keeps it impossible for the
        annotations and :data:`DESTRUCTIVE` / :data:`READ_ONLY` to drift apart.
        Copies rather than mutating the shared registry objects.

        Three tiers, because "not destructive" does not mean "safe":

        * :data:`DESTRUCTIVE` — prompts, and the call itself needs a token.
        * :data:`READ_ONLY` — safe to call unattended; this is what lets plan
          mode skip the prompt.
        * everything else — writes that are not catastrophic (``cpp_create``,
          ``apps_sync``…). Explicitly ``readOnlyHint=False`` so they keep
          prompting. Leaving them null would work too, but saying it outright
          means no tool ships with an unanswered question about it.
        """
        tools = await call_next(context)
        return [
            tool.model_copy(update={"annotations": _annotations_for(tool.name)})
            for tool in tools
        ]

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        tool = context.message.name
        if tool not in DESTRUCTIVE:
            return await call_next(context)

        arguments = dict(context.message.arguments or {})
        token = arguments.pop(CONFIRM_ARG, None)
        user_id = _user_id()
        fingerprint = _fingerprint(tool, arguments)

        if not token:
            raise ToolError(_challenge(tool, arguments,
                                       _mint(tool, fingerprint, user_id)))

        # Pop first: a token is spent by being presented, so a mismatch cannot
        # be retried against a different argument set with the same token.
        pending = _pending.pop(str(token), None)
        now = time.monotonic()
        if pending is None:
            raise ToolError(
                f"Consent token for {tool} is unknown or already used. Every "
                f"destructive call needs its own token — call {tool} again "
                f"without {CONFIRM_ARG} to get a fresh one."
            )
        if pending.expires_at <= now:
            raise ToolError(
                f"Consent token for {tool} expired. Call it again without "
                f"{CONFIRM_ARG} to get a fresh one."
            )
        if pending.user_id != user_id:
            raise ToolError("Consent token was issued to a different user.")
        if pending.tool != tool or pending.fingerprint != fingerprint:
            raise ToolError(
                f"Consent token does not match this call. It was issued for "
                f"{pending.tool} with different arguments; consent covers one "
                f"exact call. Call {tool} again without {CONFIRM_ARG}."
            )

        # Hand the tool its own arguments — `confirm` is ours, not part of any
        # tool's schema.
        cleaned = context.message.model_copy(update={"arguments": arguments})
        return await call_next(context.copy(message=cleaned))
