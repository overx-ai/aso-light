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


class ConsentGate(Middleware):
    """Refuse destructive tool calls that carry no matching consent token."""

    async def on_list_tools(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        """Stamp ``destructiveHint`` on every gated tool.

        This is the protocol's own marker: MCP clients read it to decide whether
        to prompt a human before a call. Doing it here rather than on 35
        decorators keeps it impossible for the annotations and :data:`DESTRUCTIVE`
        to drift apart. Copies rather than mutating the shared registry objects.
        """
        tools = await call_next(context)
        return [
            tool.model_copy(update={"annotations": ToolAnnotations(
                destructiveHint=True, readOnlyHint=False, idempotentHint=False)})
            if tool.name in DESTRUCTIVE else tool
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
