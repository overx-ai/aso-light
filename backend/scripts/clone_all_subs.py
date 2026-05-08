"""Batch version-bump every subscription in every app under one ASC credential.

Drives the existing /clone endpoints — no new feature code, just orchestration.

**Cascade prevention**: a sub is eligible to bump only when no other sub
in its group is its predecessor. This keeps re-runs idempotent: once
``foo`` has been bumped to ``foo.v2``, ``foo.v2`` itself isn't bumped to
``foo.v3`` on the next invocation.

Usage:
    # Dry run (preview only, no ASC writes):
    uv run python scripts/clone_all_subs.py --dry-run

    # Apply, all apps under all credentials:
    uv run python scripts/clone_all_subs.py --apply

    # Restrict to one credential or one app:
    uv run python scripts/clone_all_subs.py --apply --credential-id 3
    uv run python scripts/clone_all_subs.py --apply --app-id 12

    # Pass an existing JWT instead of email/password:
    uv run python scripts/clone_all_subs.py --apply --token "$ASOLIGHT_TOKEN"

Auth:
    --token              raw JWT (skip login)
    ASOLIGHT_EMAIL       login email (env or --email)
    ASOLIGHT_PASSWORD    login password (env or --password)

The clone endpoint runs synchronously server-side and returns the final
operation status, so no polling loop is needed. For partial failures the
script prints the per-step error_log; you can re-run the script (cloners
skip already-completed steps) or hit
``POST /apps/{id}/clone-operations/{op_id}/retry``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any

import httpx


_DOT_VERSION_RE = re.compile(r"^(?P<base>.+)\.v(?P<n>\d+)$")
_UNDERSCORE_VERSION_RE = re.compile(r"^(?P<base>.+?)_v(?P<n>\d+)$")


def _next_version(product_id: str) -> str:
    """Mirror of backend ``next_versioned_product_id`` (kept in sync)."""
    m = _DOT_VERSION_RE.match(product_id)
    if m is not None:
        return f"{m.group('base')}.v{int(m.group('n')) + 1}"
    m = _UNDERSCORE_VERSION_RE.match(product_id)
    if m is not None:
        return f"{m.group('base')}_v{int(m.group('n')) + 1}"
    return f"{product_id}.v2"


def _has_precursor(pid: str, all_pids: set[str]) -> bool:
    """True iff a productId one version below ``pid`` is in ``all_pids``.

    Used to identify accidental cascade-bumps. e.g., ``foo.v4`` has
    precursor ``foo.v3``; if ``foo.v3`` exists in the group, ``foo.v4``
    is itself a clone of something else.
    """
    m = _DOT_VERSION_RE.match(pid)
    if m is not None:
        n = int(m.group("n"))
        base = m.group("base")
        prev = base if n == 2 else f"{base}.v{n - 1}"
        return prev in all_pids
    m = _UNDERSCORE_VERSION_RE.match(pid)
    if m is not None:
        n = int(m.group("n"))
        base = m.group("base")
        prev = base if n == 2 else f"{base}_v{n - 1}"
        return prev in all_pids
    return False


def _orphan_pids(all_pids: set[str]) -> set[str]:
    """Identify "depth-2+ bumps" — clones of clones.

    A pid is an orphan if its precursor itself has a precursor in the
    same group. Keeps the original and the first bump; flags anything
    further along the chain.
    """
    orphans: set[str] = set()
    for pid in all_pids:
        if not _has_precursor(pid, all_pids):
            continue
        # find the precursor and check if it has its own precursor
        m = _DOT_VERSION_RE.match(pid)
        if m is not None:
            n = int(m.group("n"))
            base = m.group("base")
            prev = base if n == 2 else f"{base}.v{n - 1}"
        else:
            m = _UNDERSCORE_VERSION_RE.match(pid)
            assert m is not None  # _has_precursor was True
            n = int(m.group("n"))
            base = m.group("base")
            prev = base if n == 2 else f"{base}_v{n - 1}"
        if _has_precursor(prev, all_pids):
            orphans.add(pid)
    return orphans


DEFAULT_BASE_URL = os.getenv("ASOLIGHT_BASE_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def list_apps(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get("/api/v1/apps")
    resp.raise_for_status()
    return resp.json()


async def list_subscriptions(
    client: httpx.AsyncClient, app_id: int,
) -> list[dict[str, Any]]:
    """Returns flat list of {id, name, product_id, group_name}."""
    resp = await client.get(f"/api/v1/apps/{app_id}/subscriptions")
    resp.raise_for_status()
    groups = resp.json()
    flat: list[dict[str, Any]] = []
    for g in groups:
        for s in g.get("subscriptions", []):
            flat.append({**s, "group_name": g["name"]})
    return flat


async def delete_subscription(
    client: httpx.AsyncClient, app_id: int, sub_id: int,
) -> tuple[bool, str]:
    """DELETE a subscription. Returns (ok, detail)."""
    resp = await client.delete(
        f"/api/v1/apps/{app_id}/subscriptions/{sub_id}",
    )
    if resp.status_code == 204:
        return True, "deleted"
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


# ---------------------------------------------------------------------------
# Clone operations
# ---------------------------------------------------------------------------


async def preview_sub_clone(
    client: httpx.AsyncClient, app_id: int, sub_id: int,
) -> dict[str, Any]:
    resp = await client.get(
        f"/api/v1/apps/{app_id}/subscriptions/{sub_id}/clone/preview",
    )
    resp.raise_for_status()
    return resp.json()


async def clone_sub(
    client: httpx.AsyncClient,
    app_id: int,
    sub_id: int,
    new_product_id: str,
    swap_rc: bool,
) -> dict[str, Any]:
    body = {
        "new_product_id": new_product_id,
        # ASC enforces unique reference name within an app — reuse the
        # bumped productId so the name matches the new resource and the
        # source name stays attached to the (auto-archived) old sub.
        "new_name": new_product_id,
        "scope": {
            "localizations": True,
            "price_schedule": True,
            "intro_offers": True,
            "screenshot": True,
            "auto_archive": True,
            "group_availability": True,
        },
        "swap_revenuecat": swap_rc,
    }
    resp = await client.post(
        f"/api/v1/apps/{app_id}/subscriptions/{sub_id}/clone",
        json=body,
        timeout=httpx.Timeout(600.0, connect=30.0),
    )
    if resp.status_code >= 400:
        return {
            "status": "failed",
            "error_log": [f"HTTP {resp.status_code}: {resp.text[:300]}"],
            "asc_steps": [],
            "revenuecat_steps": [],
            "id": None,
        }
    return resp.json()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fmt_preview(sub: dict[str, Any], preview: dict[str, Any]) -> str:
    rc = ""
    if preview["revenuecat_connected"]:
        rc = (
            f"  RC: linked, old_found={preview['revenuecat_old_product_found']}, "
            f"ent={preview['revenuecat_attached_entitlements']}, "
            f"pkg={preview['revenuecat_attached_packages']}"
        )
    return (
        f"  sub={sub['id']} {sub['product_id']} → {preview['suggested_product_id']}"
        f"  (locales={preview['locale_count']}"
        f", territories={preview['priced_territory_count']}"
        f", intro={preview['intro_offer_count']}"
        f", screenshot={'y' if preview['has_screenshot'] else 'n'})"
        f"{rc}"
    )


def fmt_op_outcome(op: dict[str, Any]) -> str:
    parts = [f"status={op.get('status', '?')}"]
    if op.get("id") is not None:
        parts.append(f"op_id={op['id']}")
    if op.get("target_asc_id"):
        parts.append(f"asc={op['target_asc_id']}")
    line = "    " + " ".join(parts)
    errs = op.get("error_log") or []
    if errs:
        line += "\n      errors:"
        for e in errs:
            line += f"\n        - {e}"
    return line


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _delete_orphans(
    client: httpx.AsyncClient,
    apps: list[dict[str, Any]],
    confirm: bool,
) -> int:
    print("=== Delete orphan version-bumps ===")
    targets: list[tuple[dict, dict]] = []  # (app, sub)
    for app in apps:
        subs = await list_subscriptions(client, app["id"])
        all_pids = {s["product_id"] for s in subs}
        orphans = _orphan_pids(all_pids)
        for s in subs:
            if s["product_id"] in orphans:
                targets.append((app, s))

    if not targets:
        print("No orphans found.")
        return 0

    print(f"Found {len(targets)} orphan sub(s):")
    for app, sub in targets:
        print(
            f"  - app={app['id']} ({app['bundle_id']}) "
            f"sub_id={sub['id']} {sub['product_id']}"
        )

    if confirm:
        ans = input("\nProceed with deletion? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1

    print()
    failures: list[str] = []
    for app, sub in targets:
        ok, detail = await delete_subscription(client, app["id"], sub["id"])
        marker = "deleted" if ok else "FAILED"
        print(f"  {marker}: {sub['product_id']} ({detail})")
        if not ok:
            failures.append(f"{sub['product_id']}: {detail}")

    print()
    print(f"=== Done: {len(targets) - len(failures)}/{len(targets)} deleted ===")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f}")
        return 2
    return 0


async def run(args: argparse.Namespace) -> int:
    headers = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=headers,
        timeout=httpx.Timeout(60.0, connect=10.0),
    ) as client:
        if not args.token:
            email = args.email or os.environ.get("ASOLIGHT_EMAIL")
            password = args.password or os.environ.get("ASOLIGHT_PASSWORD")
            if not email or not password:
                print(
                    "error: provide --token, or --email/--password "
                    "(or ASOLIGHT_EMAIL/ASOLIGHT_PASSWORD env vars)",
                    file=sys.stderr,
                )
                return 2
            token = await login(client, email, password)
            client.headers["Authorization"] = f"Bearer {token}"

        apps = await list_apps(client)
        if args.credential_id is not None:
            apps = [a for a in apps if a["credential_id"] == args.credential_id]
        if args.app_id is not None:
            apps = [a for a in apps if a["id"] == args.app_id]

        if not apps:
            print("No apps match filters.")
            return 0

        if args.delete_orphans:
            return await _delete_orphans(client, apps, confirm=not args.yes)

        mode = "DRY-RUN" if args.dry_run else "APPLY"
        print(f"=== Clone all subscriptions [{mode}] ===")
        print(f"Apps: {len(apps)}")
        for a in apps:
            print(f"  - {a['id']} {a['name']} ({a['bundle_id']})")
        print()

        summary: list[tuple[dict, list[tuple[str, dict]]]] = []

        for app in apps:
            print(f"App {app['id']} {app['name']} ({app['bundle_id']}):")
            subs = await list_subscriptions(client, app["id"])
            if not subs:
                print("  (no subscriptions)\n")
                summary.append((app, []))
                continue

            # Cascade prevention: a sub is itself a clone if some other
            # sub in the same group has it as a next-version successor.
            # Skip those — they were created by a previous bump and we
            # never want to bump a bump.
            existing_pids = {s["product_id"] for s in subs}
            successor_pids = {_next_version(s["product_id"]) for s in subs}
            clone_pids = existing_pids & successor_pids
            originals = [s for s in subs if s["product_id"] not in clone_pids]
            skipped = [s for s in subs if s["product_id"] in clone_pids]
            if skipped:
                print(
                    f"  skipping {len(skipped)} bumped sub(s): "
                    + ", ".join(s["product_id"] for s in skipped)
                )

            results: list[tuple[str, dict]] = []
            for sub in originals:
                try:
                    preview = await preview_sub_clone(client, app["id"], sub["id"])
                except httpx.HTTPStatusError as e:
                    print(f"  sub={sub['id']} preview failed: {e}")
                    results.append((sub["product_id"], {
                        "status": "failed",
                        "error_log": [f"preview: {e}"],
                    }))
                    continue

                print(fmt_preview(sub, preview))

                if args.dry_run:
                    results.append((sub["product_id"], {"status": "dry-run"}))
                    continue

                op = await clone_sub(
                    client,
                    app["id"],
                    sub["id"],
                    preview["suggested_product_id"],
                    swap_rc=not args.no_rc,
                )
                print(fmt_op_outcome(op))
                results.append((sub["product_id"], op))

            print()
            summary.append((app, results))

        # Final summary
        print("=== Summary ===")
        for app, results in summary:
            counts = {"done": 0, "partial": 0, "failed": 0, "dry-run": 0}
            for _, op in results:
                s = op.get("status", "failed")
                counts[s] = counts.get(s, 0) + 1
            buckets = ", ".join(
                f"{k}={v}" for k, v in counts.items() if v
            ) or "no subs"
            print(f"  App {app['id']} ({app['bundle_id']}): {buckets}")
            for product_id, op in results:
                if op.get("status") in ("partial", "failed"):
                    op_id = op.get("id")
                    print(
                        f"    - {product_id} "
                        f"{'op_id=' + str(op_id) + ' ' if op_id else ''}"
                        f"{op['status']}: {op.get('error_log') or []}"
                    )

        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--token", help="JWT bearer token (skip login)")
    p.add_argument("--email", help="Login email (or ASOLIGHT_EMAIL env)")
    p.add_argument("--password", help="Login password (or ASOLIGHT_PASSWORD env)")
    p.add_argument("--credential-id", type=int, help="Restrict to one ASC credential")
    p.add_argument("--app-id", type=int, help="Restrict to one app")
    p.add_argument("--no-rc", action="store_true", help="Skip RevenueCat swap")

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview only")
    mode.add_argument("--apply", action="store_true", help="Execute clones")
    mode.add_argument(
        "--delete-orphans",
        action="store_true",
        help=(
            "Delete depth-2+ version-bump shells (e.g. v4, v5 when only "
            "v2->v3 was intended). Lists targets first; pass --yes to skip "
            "the confirmation prompt."
        ),
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive confirm in --delete-orphans mode",
    )

    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
