"""Swap an old ASC productId for a new one across RevenueCat.

Strategy chosen by the user (see plan doc): keep entitlements/offerings
intact, swap the underlying product attachments. Existing subscribers
keep their entitlement, the mobile app's offering identifiers don't
change, and the only thing that flips is which store_identifier the
package/entitlement points at.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.revenuecat.client import RevenueCatClient
from app.services.revenuecat.errors import RevenueCatAPIError

logger = logging.getLogger(__name__)


class RevenueCatProductSwap:
    def __init__(self, client: RevenueCatClient, rc_app_id: str | None):
        self.client = client
        self.rc_app_id = rc_app_id

    async def find_product_by_store_id(
        self, store_identifier: str,
    ) -> dict | None:
        """Return the first matching RC product or None."""
        products = await self.client.list_products(
            app_id=self.rc_app_id,
            store_identifier=store_identifier,
        )
        for p in products:
            if p.get("store_identifier") == store_identifier:
                return p
        return None

    async def swap(
        self,
        old_store_id: str,
        new_store_id: str,
        product_type: str = "subscription",
        subscription_period: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Run the full swap.

        Returns:
            Dict with per-step status (used by the orchestrator to write
            progress into the CloneOperation row).
        """
        steps: dict[str, Any] = {
            "find_old_product": {"status": "pending"},
            "create_new_product": {"status": "pending"},
            "attach_to_entitlements": {"status": "pending", "completed": 0, "total": 0},
            "attach_to_packages": {"status": "pending", "completed": 0, "total": 0},
            "detach_old_from_entitlements": {"status": "pending", "completed": 0, "total": 0},
            "detach_old_from_packages": {"status": "pending", "completed": 0, "total": 0},
            "archive_old_product": {"status": "pending"},
        }
        errors: list[str] = []

        # 1. Find the old product
        steps["find_old_product"]["status"] = "running"
        try:
            old_product = await self.find_product_by_store_id(old_store_id)
        except RevenueCatAPIError as exc:
            steps["find_old_product"].update(status="failed", detail=str(exc))
            errors.append(f"find_old_product: {exc}")
            return {"steps": steps, "errors": errors}
        if old_product is None:
            steps["find_old_product"].update(
                status="skipped",
                detail=f"No RC product matches store_id={old_store_id!r}",
            )
            return {"steps": steps, "errors": errors}
        steps["find_old_product"].update(
            status="done", detail=f"product_id={old_product['id']}",
        )

        old_product_id = old_product["id"]
        old_app_id = old_product.get("app_id") or self.rc_app_id
        if old_app_id is None:
            steps["create_new_product"].update(
                status="failed",
                detail="No RC app_id available; set rc_app_id on the credential",
            )
            errors.append("create_new_product: missing rc_app_id")
            return {"steps": steps, "errors": errors}

        # 2. Create new product mirror
        steps["create_new_product"]["status"] = "running"
        try:
            existing_new = await self.find_product_by_store_id(new_store_id)
            if existing_new:
                new_product = existing_new
                steps["create_new_product"].update(
                    status="done",
                    detail=(
                        f"existing product_id={new_product['id']} "
                        f"(reused — idempotent re-run)"
                    ),
                )
            else:
                new_product = await self.client.create_product(
                    store_identifier=new_store_id,
                    app_id=old_app_id,
                    product_type=product_type,
                    display_name=display_name or old_product.get("display_name"),
                    subscription_period=subscription_period,
                )
                steps["create_new_product"].update(
                    status="done", detail=f"product_id={new_product['id']}",
                )
        except RevenueCatAPIError as exc:
            steps["create_new_product"].update(status="failed", detail=str(exc))
            errors.append(f"create_new_product: {exc}")
            return {"steps": steps, "errors": errors}
        new_product_id = new_product["id"]

        # 3. Discover entitlements + packages that reference the old product
        try:
            entitlements = await self.client.list_entitlements()
            offerings = await self.client.list_offerings()
        except RevenueCatAPIError as exc:
            errors.append(f"discover_attachments: {exc}")
            steps["attach_to_entitlements"].update(
                status="failed", detail=str(exc),
            )
            return {"steps": steps, "errors": errors}

        attached_entitlement_ids: list[str] = []
        for ent in entitlements:
            for prod in (ent.get("products") or []):
                if prod.get("id") == old_product_id or (
                    prod.get("store_identifier") == old_store_id
                ):
                    attached_entitlement_ids.append(ent["id"])
                    break

        attached_packages: list[tuple[str, str]] = []  # (offering_id, package_id)
        for off in offerings:
            try:
                packages = await self.client.list_packages(off["id"])
            except RevenueCatAPIError:
                continue
            for pkg in packages:
                for prod in (pkg.get("products") or []):
                    if prod.get("id") == old_product_id or (
                        prod.get("store_identifier") == old_store_id
                    ):
                        attached_packages.append((off["id"], pkg["id"]))
                        break

        # 4. Attach new product to those entitlements
        steps["attach_to_entitlements"].update(
            total=len(attached_entitlement_ids), status="running",
        )
        for ent_id in attached_entitlement_ids:
            try:
                await self.client.attach_products_to_entitlement(
                    ent_id, [new_product_id],
                )
                steps["attach_to_entitlements"]["completed"] += 1
            except RevenueCatAPIError as exc:
                errors.append(
                    f"attach_to_entitlements[{ent_id}]: {exc}"
                )
        steps["attach_to_entitlements"]["status"] = (
            "done"
            if steps["attach_to_entitlements"]["completed"]
            == steps["attach_to_entitlements"]["total"]
            else "partial"
        )

        # 5. Attach new product to packages
        steps["attach_to_packages"].update(
            total=len(attached_packages), status="running",
        )
        for off_id, pkg_id in attached_packages:
            try:
                await self.client.attach_products_to_package(
                    off_id, pkg_id, [new_product_id],
                )
                steps["attach_to_packages"]["completed"] += 1
            except RevenueCatAPIError as exc:
                errors.append(
                    f"attach_to_packages[{off_id}/{pkg_id}]: {exc}"
                )
        steps["attach_to_packages"]["status"] = (
            "done"
            if steps["attach_to_packages"]["completed"]
            == steps["attach_to_packages"]["total"]
            else "partial"
        )

        # 6. Detach old product
        steps["detach_old_from_entitlements"].update(
            total=len(attached_entitlement_ids), status="running",
        )
        for ent_id in attached_entitlement_ids:
            try:
                await self.client.detach_products_from_entitlement(
                    ent_id, [old_product_id],
                )
                steps["detach_old_from_entitlements"]["completed"] += 1
            except RevenueCatAPIError as exc:
                errors.append(
                    f"detach_old_from_entitlements[{ent_id}]: {exc}"
                )
        steps["detach_old_from_entitlements"]["status"] = (
            "done"
            if steps["detach_old_from_entitlements"]["completed"]
            == steps["detach_old_from_entitlements"]["total"]
            else "partial"
        )

        steps["detach_old_from_packages"].update(
            total=len(attached_packages), status="running",
        )
        for off_id, pkg_id in attached_packages:
            try:
                await self.client.detach_products_from_package(
                    off_id, pkg_id, [old_product_id],
                )
                steps["detach_old_from_packages"]["completed"] += 1
            except RevenueCatAPIError as exc:
                errors.append(
                    f"detach_old_from_packages[{off_id}/{pkg_id}]: {exc}"
                )
        steps["detach_old_from_packages"]["status"] = (
            "done"
            if steps["detach_old_from_packages"]["completed"]
            == steps["detach_old_from_packages"]["total"]
            else "partial"
        )

        # 7. Archive old product
        steps["archive_old_product"]["status"] = "running"
        try:
            await self.client.archive_product(old_product_id)
            steps["archive_old_product"].update(
                status="done", detail=f"archived product_id={old_product_id}",
            )
        except RevenueCatAPIError as exc:
            errors.append(f"archive_old_product: {exc}")
            steps["archive_old_product"].update(status="failed", detail=str(exc))

        return {
            "steps": steps,
            "errors": errors,
            "old_product_id": old_product_id,
            "new_product_id": new_product_id,
            "attached_entitlements": attached_entitlement_ids,
            "attached_packages": [
                {"offering_id": off, "package_id": pkg}
                for off, pkg in attached_packages
            ],
        }
