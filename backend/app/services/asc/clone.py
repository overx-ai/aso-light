"""Clone-and-version-bump service for ASC subscriptions and IAPs.

Apple's ``productId`` is immutable, and subs that are stuck in
``WAITING_FOR_REVIEW`` block new app version submission. The fix is to
mint a new product (``original_id_v2``) with all the same metadata,
prices, intro offers, and screenshot — then submit the new product for
review while taking the old one off sale.

This module orchestrates the recreate. Each step is independent so a
partial failure (e.g. one localization rejected) leaves the operation
in ``partial`` state and exposes a per-step retry.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.territories import ALPHA2_TO_ALPHA3
from app.models.iap import IAPPrice, InAppPurchase
from app.models.subscription import (
    Subscription,
    SubscriptionGroup,
    SubscriptionPrice,
)
from app.models.territory import Territory
from app.services.asc.errors import ASCAPIError

if TYPE_CHECKING:
    from app.services.asc.pricing import ASCPricingService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# productId version-suffix helpers
# ---------------------------------------------------------------------------


_VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+?)_v(?P<n>\d+)$")


def next_versioned_product_id(product_id: str) -> str:
    """Return the next ``_v{n}`` suffix for a productId.

    ``com.app.pro``       -> ``com.app.pro_v2``
    ``com.app.pro_v2``    -> ``com.app.pro_v3``
    ``com.app.pro_v9``    -> ``com.app.pro_v10``

    Suffix is always ``_v{n}`` (lowercase); we never alter the base.
    """
    match = _VERSION_SUFFIX_RE.match(product_id)
    if match is None:
        return f"{product_id}_v2"
    base = match.group("base")
    n = int(match.group("n"))
    return f"{base}_v{n + 1}"


# ---------------------------------------------------------------------------
# Step status helper
# ---------------------------------------------------------------------------


def _step(name: str, status: str = "pending", **extra: Any) -> dict:
    out = {"name": name, "status": status}
    out.update(extra)
    return out


def _alpha2(alpha3: str) -> str | None:
    """Reverse ALPHA2_TO_ALPHA3 lookup. None if not in our table."""
    for a2, a3 in ALPHA2_TO_ALPHA3.items():
        if a3 == alpha3:
            return a2
    return None


# ---------------------------------------------------------------------------
# Subscription cloner
# ---------------------------------------------------------------------------


class SubscriptionCloner:
    def __init__(
        self,
        pricing: ASCPricingService,
        session: AsyncSession,
        app_id: int,
        app_asc_id: str,
    ):
        self.pricing = pricing
        self.session = session
        self.app_id = app_id
        self.app_asc_id = app_asc_id

    async def clone(
        self,
        source: Subscription,
        new_product_id: str,
        new_name: str | None,
        scope: dict,
    ) -> dict:
        """Run the clone. Returns dict with new sub + step statuses.

        Idempotent: if the new productId already exists in the source's
        group, we resume from the next step.
        """
        steps: list[dict] = []
        errors: list[str] = []
        new_asc_id: str | None = None

        # Step 1: read source detail from ASC + DB
        steps.append(_step("read_source", "running"))
        try:
            detail = await self.pricing.get_subscription_detail(
                source.asc_subscription_id,
            )
            attrs = detail.get("attributes", {})
            period = attrs.get("subscriptionPeriod") or "ONE_MONTH"
            family_sharable = bool(attrs.get("familySharable", False))
            group_level = int(attrs.get("groupLevel", 1))
            review_note = attrs.get("reviewNote")
            source_name = attrs.get("name") or source.name
            steps[-1] = _step(
                "read_source", "done",
                detail=(
                    f"period={period} familySharable={family_sharable} "
                    f"groupLevel={group_level}"
                ),
            )
        except ASCAPIError as exc:
            steps[-1] = _step("read_source", "failed", detail=str(exc))
            errors.append(f"read_source: {exc}")
            return {
                "steps": steps, "errors": errors, "target_asc_id": None,
            }

        # Group lookup
        result = await self.session.execute(
            select(SubscriptionGroup).where(
                SubscriptionGroup.id == source.group_id,
            )
        )
        group = result.scalar_one()

        # Step 2: idempotency check — does target product_id already exist?
        steps.append(_step("create_subscription", "running"))
        existing = await self.pricing.list_subscriptions(group.asc_group_id)
        for s in existing:
            if s.get("attributes", {}).get("productId") == new_product_id:
                new_asc_id = s["id"]
                steps[-1] = _step(
                    "create_subscription", "done",
                    detail=(
                        f"already exists asc_id={new_asc_id} "
                        f"(idempotent re-run)"
                    ),
                )
                break
        if new_asc_id is None:
            try:
                created = await self.pricing.create_subscription(
                    group_id=group.asc_group_id,
                    product_id=new_product_id,
                    name=new_name or source_name,
                    period=period,
                    family_sharable=family_sharable,
                    group_level=group_level,
                    review_note=review_note,
                )
                new_asc_id = created.get("data", {}).get("id")
                if new_asc_id is None:
                    raise ASCAPIError(500, {
                        "errors": [{"detail": "ASC returned no id"}],
                    })
                steps[-1] = _step(
                    "create_subscription", "done",
                    detail=f"asc_id={new_asc_id}",
                )
            except ASCAPIError as exc:
                steps[-1] = _step(
                    "create_subscription", "failed", detail=str(exc),
                )
                errors.append(f"create_subscription: {exc}")
                return {
                    "steps": steps, "errors": errors, "target_asc_id": None,
                }

        # Persist new local Subscription row
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.asc_subscription_id == new_asc_id,
            )
        )
        new_local = result.scalar_one_or_none()
        if new_local is None:
            new_local = Subscription(
                group_id=group.id,
                asc_subscription_id=new_asc_id,
                name=new_name or source_name,
                product_id=new_product_id,
            )
            self.session.add(new_local)
            await self.session.flush()

        # Step 3: availability (mirror source territories)
        if scope.get("group_availability", True):
            steps.append(_step("availability", "running"))
            try:
                source_territories = await (
                    self.pricing.list_subscription_availability(
                        source.asc_subscription_id,
                    )
                )
                if source_territories:
                    await self.pricing.create_subscription_availability(
                        subscription_id=new_asc_id,
                        available_alpha3_codes=source_territories,
                    )
                    steps[-1] = _step(
                        "availability", "done",
                        detail=f"{len(source_territories)} territories",
                    )
                else:
                    steps[-1] = _step(
                        "availability", "skipped",
                        detail="source had no territories",
                    )
            except ASCAPIError as exc:
                # availability already exists is OK on re-run
                if exc.status_code == 409:
                    steps[-1] = _step(
                        "availability", "skipped",
                        detail="already exists (idempotent)",
                    )
                else:
                    steps[-1] = _step(
                        "availability", "failed", detail=str(exc),
                    )
                    errors.append(f"availability: {exc}")

        # Step 4: localizations
        if scope.get("localizations", True):
            try:
                source_locs = await (
                    self.pricing.list_subscription_localizations(
                        source.asc_subscription_id,
                    )
                )
                target_locs = await (
                    self.pricing.list_subscription_localizations(new_asc_id)
                )
                target_locales = {
                    (loc.get("attributes") or {}).get("locale")
                    for loc in target_locs
                }
                steps.append(_step(
                    "localizations", "running",
                    completed=0, total=len(source_locs),
                ))
                for loc in source_locs:
                    a = loc.get("attributes") or {}
                    locale = a.get("locale")
                    if not locale or locale in target_locales:
                        steps[-1]["completed"] += 1
                        continue
                    try:
                        await (
                            self.pricing.create_subscription_localization(
                                subscription_id=new_asc_id,
                                locale=locale,
                                name=a.get("name") or "",
                                description=a.get("description") or "",
                            )
                        )
                        steps[-1]["completed"] += 1
                    except ASCAPIError as exc:
                        errors.append(f"localizations[{locale}]: {exc}")
                steps[-1]["status"] = (
                    "done"
                    if steps[-1]["completed"] == steps[-1]["total"]
                    else "partial"
                )
            except ASCAPIError as exc:
                steps.append(_step(
                    "localizations", "failed", detail=str(exc),
                ))
                errors.append(f"localizations: {exc}")

        # Step 5: prices (territory -> price_point)
        if scope.get("price_schedule", True):
            res = await self.session.execute(
                select(SubscriptionPrice).where(
                    SubscriptionPrice.subscription_id == source.id,
                )
            )
            source_prices = res.scalars().all()
            steps.append(_step(
                "prices", "running",
                completed=0, total=len(source_prices),
            ))
            for sp in source_prices:
                if sp.price_point_id is None:
                    steps[-1]["completed"] += 1
                    continue
                try:
                    await self.pricing.create_subscription_price(
                        subscription_id=new_asc_id,
                        price_point_id=sp.price_point_id,
                    )
                    steps[-1]["completed"] += 1
                except ASCAPIError as exc:
                    errors.append(
                        f"prices[territory_id={sp.territory_id}]: {exc}"
                    )
            steps[-1]["status"] = (
                "done"
                if steps[-1]["completed"] == steps[-1]["total"]
                else "partial"
            )

        # Step 6: intro offers
        if scope.get("intro_offers", True):
            try:
                offers = await (
                    self.pricing.list_subscription_introductory_offers(
                        source.asc_subscription_id,
                    )
                )
                steps.append(_step(
                    "intro_offers", "running",
                    completed=0, total=len(offers),
                ))
                for entry in offers:
                    res = entry["resource"]
                    a = res.get("attributes", {})
                    rels = res.get("relationships", {})
                    territory_ref = rels.get("territory", {}).get("data")
                    pp_ref = (
                        rels.get("subscriptionPricePoint", {}).get("data")
                    )
                    territory_id = (
                        territory_ref.get("id") if territory_ref else None
                    )
                    price_point_id = pp_ref.get("id") if pp_ref else None
                    try:
                        await (
                            self.pricing
                            .create_subscription_introductory_offer(
                                subscription_id=new_asc_id,
                                offer_mode=a.get("offerMode"),
                                duration=a.get("duration"),
                                number_of_periods=int(
                                    a.get("numberOfPeriods", 1),
                                ),
                                territory_id=territory_id,
                                price_point_id=price_point_id,
                                start_date=a.get("startDate"),
                                end_date=a.get("endDate"),
                            )
                        )
                        steps[-1]["completed"] += 1
                    except ASCAPIError as exc:
                        errors.append(f"intro_offers: {exc}")
                steps[-1]["status"] = (
                    "done"
                    if steps[-1]["completed"] == steps[-1]["total"]
                    else "partial"
                )
            except ASCAPIError as exc:
                steps.append(_step(
                    "intro_offers", "failed", detail=str(exc),
                ))
                errors.append(f"intro_offers: {exc}")

        # Step 7: review screenshot
        if scope.get("screenshot", True):
            steps.append(_step("screenshot", "running"))
            try:
                src_shot = await self.pricing.get_subscription_review_screenshot(
                    source.asc_subscription_id,
                )
                if src_shot is None:
                    steps[-1] = _step(
                        "screenshot", "skipped",
                        detail="no source screenshot",
                    )
                else:
                    file_bytes = await _download_screenshot_asset(src_shot)
                    if file_bytes is None:
                        steps[-1] = _step(
                            "screenshot", "skipped",
                            detail="no downloadable URL on source",
                        )
                    else:
                        attrs = src_shot.get("attributes", {})
                        file_name = attrs.get("fileName") or "screenshot.png"
                        await (
                            self.pricing
                            .upload_subscription_review_screenshot(
                                subscription_id=new_asc_id,
                                file_name=file_name,
                                file_bytes=file_bytes,
                            )
                        )
                        steps[-1] = _step(
                            "screenshot", "done",
                            detail=f"{len(file_bytes)} bytes",
                        )
            except ASCAPIError as exc:
                steps[-1] = _step("screenshot", "failed", detail=str(exc))
                errors.append(f"screenshot: {exc}")

        # Step 8: archive source by removing all territories
        if scope.get("auto_archive", True):
            steps.append(_step("archive_source", "running"))
            try:
                await self.pricing.update_subscription_availability(
                    subscription_id=source.asc_subscription_id,
                    available_alpha3_codes=[],
                    available_in_new_territories=False,
                )
                steps[-1] = _step(
                    "archive_source", "done",
                    detail=(
                        "source removed from sale (existing subscribers "
                        "unaffected)"
                    ),
                )
            except ASCAPIError as exc:
                steps[-1] = _step(
                    "archive_source", "failed", detail=str(exc),
                )
                errors.append(f"archive_source: {exc}")

        return {
            "steps": steps,
            "errors": errors,
            "target_asc_id": new_asc_id,
            "target_local_id": new_local.id if new_local else None,
            "subscription_period": period,
        }


# ---------------------------------------------------------------------------
# IAP cloner
# ---------------------------------------------------------------------------


class IAPCloner:
    def __init__(
        self,
        pricing: ASCPricingService,
        session: AsyncSession,
        app_id: int,
        app_asc_id: str,
    ):
        self.pricing = pricing
        self.session = session
        self.app_id = app_id
        self.app_asc_id = app_asc_id

    async def clone(
        self,
        source: InAppPurchase,
        new_product_id: str,
        new_name: str | None,
        scope: dict,
    ) -> dict:
        steps: list[dict] = []
        errors: list[str] = []
        new_asc_id: str | None = None

        # Step 1: read source detail
        steps.append(_step("read_source", "running"))
        try:
            detail = await self.pricing.get_iap_detail(source.asc_iap_id)
            attrs = detail.get("attributes", {})
            iap_type = attrs.get("inAppPurchaseType") or source.iap_type
            review_note = attrs.get("reviewNote")
            family_sharable = bool(attrs.get("familyShareable", False))
            source_name = attrs.get("name") or source.name
            steps[-1] = _step(
                "read_source", "done",
                detail=f"type={iap_type} familyShareable={family_sharable}",
            )
        except ASCAPIError as exc:
            steps[-1] = _step("read_source", "failed", detail=str(exc))
            errors.append(f"read_source: {exc}")
            return {
                "steps": steps, "errors": errors, "target_asc_id": None,
            }

        # Step 2: idempotency check
        steps.append(_step("create_iap", "running"))
        all_iaps = await self.pricing.list_iaps(self.app_asc_id)
        for entry in all_iaps:
            if entry.get("attributes", {}).get("productId") == new_product_id:
                new_asc_id = entry["id"]
                steps[-1] = _step(
                    "create_iap", "done",
                    detail=f"already exists asc_id={new_asc_id}",
                )
                break

        if new_asc_id is None:
            try:
                created = await self.pricing.create_iap(
                    app_id=self.app_asc_id,
                    product_id=new_product_id,
                    name=new_name or source_name,
                    iap_type=iap_type,
                    review_note=review_note,
                    family_sharable=family_sharable,
                )
                new_asc_id = created.get("id")
                if new_asc_id is None:
                    raise ASCAPIError(500, {
                        "errors": [{"detail": "ASC returned no id"}],
                    })
                steps[-1] = _step(
                    "create_iap", "done", detail=f"asc_id={new_asc_id}",
                )
            except ASCAPIError as exc:
                steps[-1] = _step("create_iap", "failed", detail=str(exc))
                errors.append(f"create_iap: {exc}")
                return {
                    "steps": steps, "errors": errors, "target_asc_id": None,
                }

        # Persist new local InAppPurchase row
        result = await self.session.execute(
            select(InAppPurchase).where(
                InAppPurchase.asc_iap_id == new_asc_id,
            )
        )
        new_local = result.scalar_one_or_none()
        if new_local is None:
            new_local = InAppPurchase(
                app_id=self.app_id,
                asc_iap_id=new_asc_id,
                name=new_name or source_name,
                product_id=new_product_id,
                iap_type=iap_type,
            )
            self.session.add(new_local)
            await self.session.flush()

        # Step 3: localizations
        if scope.get("localizations", True):
            try:
                source_locs = await self.pricing.list_iap_localizations(
                    source.asc_iap_id,
                )
                target_locs = await self.pricing.list_iap_localizations(
                    new_asc_id,
                )
                target_locales = {
                    (loc.get("attributes") or {}).get("locale")
                    for loc in target_locs
                }
                steps.append(_step(
                    "localizations", "running",
                    completed=0, total=len(source_locs),
                ))
                for loc in source_locs:
                    a = loc.get("attributes") or {}
                    locale = a.get("locale")
                    if not locale or locale in target_locales:
                        steps[-1]["completed"] += 1
                        continue
                    try:
                        await self.pricing.create_iap_localization(
                            iap_id=new_asc_id,
                            locale=locale,
                            name=a.get("name") or "",
                            description=a.get("description") or "",
                        )
                        steps[-1]["completed"] += 1
                    except ASCAPIError as exc:
                        errors.append(f"localizations[{locale}]: {exc}")
                steps[-1]["status"] = (
                    "done"
                    if steps[-1]["completed"] == steps[-1]["total"]
                    else "partial"
                )
            except ASCAPIError as exc:
                steps.append(_step(
                    "localizations", "failed", detail=str(exc),
                ))
                errors.append(f"localizations: {exc}")

        # Step 4: price schedule (single bulk call replaces all manual prices)
        if scope.get("price_schedule", True):
            steps.append(_step("price_schedule", "running"))
            res = await self.session.execute(
                select(IAPPrice, Territory)
                .join(Territory, Territory.id == IAPPrice.territory_id)
                .where(IAPPrice.iap_id == source.id)
            )
            rows = res.all()
            price_entries = [
                {
                    "territory_code": territory.code,
                    "price_point_id": iap_price.price_point_id,
                }
                for iap_price, territory in rows
                if iap_price.price_point_id
            ]
            if not price_entries:
                steps[-1] = _step(
                    "price_schedule", "skipped",
                    detail="source has no priced territories",
                )
            else:
                # Pick base territory: USA if priced, else first
                base_alpha3 = "USA"
                priced_alpha3 = {
                    ALPHA2_TO_ALPHA3.get(e["territory_code"])
                    for e in price_entries
                }
                if "USA" not in priced_alpha3:
                    base_alpha3 = next(iter(priced_alpha3))
                try:
                    await self.pricing.set_iap_price(
                        iap_id=new_asc_id,
                        price_entries=price_entries,
                        base_territory_alpha3=base_alpha3,
                    )
                    steps[-1] = _step(
                        "price_schedule", "done",
                        detail=f"{len(price_entries)} territories",
                    )
                except ASCAPIError as exc:
                    steps[-1] = _step(
                        "price_schedule", "failed", detail=str(exc),
                    )
                    errors.append(f"price_schedule: {exc}")

        # Step 5: review screenshot
        if scope.get("screenshot", True):
            steps.append(_step("screenshot", "running"))
            try:
                src_shot = await self.pricing.get_iap_review_screenshot(
                    source.asc_iap_id,
                )
                if src_shot is None:
                    steps[-1] = _step(
                        "screenshot", "skipped",
                        detail="no source screenshot",
                    )
                else:
                    file_bytes = await _download_screenshot_asset(src_shot)
                    if file_bytes is None:
                        steps[-1] = _step(
                            "screenshot", "skipped",
                            detail="no downloadable URL on source",
                        )
                    else:
                        attrs = src_shot.get("attributes", {})
                        file_name = attrs.get("fileName") or "screenshot.png"
                        await self.pricing.upload_iap_review_screenshot(
                            iap_id=new_asc_id,
                            file_name=file_name,
                            file_bytes=file_bytes,
                        )
                        steps[-1] = _step(
                            "screenshot", "done",
                            detail=f"{len(file_bytes)} bytes",
                        )
            except ASCAPIError as exc:
                steps[-1] = _step("screenshot", "failed", detail=str(exc))
                errors.append(f"screenshot: {exc}")

        # Step 6: archive source by clearing its price schedule
        if scope.get("auto_archive", True):
            steps.append(_step(
                "archive_source", "skipped",
                detail=(
                    "IAP archive via API not supported by Apple — "
                    "remove old IAP from App Version submission manually"
                ),
            ))

        return {
            "steps": steps,
            "errors": errors,
            "target_asc_id": new_asc_id,
            "target_local_id": new_local.id if new_local else None,
        }


# ---------------------------------------------------------------------------
# Screenshot binary download helper
# ---------------------------------------------------------------------------


async def _download_screenshot_asset(shot: dict) -> bytes | None:
    """Download the binary for a review screenshot.

    Apple's review screenshot resource carries an ``imageAsset`` block
    with templated URLs. We fetch the largest available rendition.
    Returns None if no usable URL is present (e.g. the source upload
    is still pending).
    """
    import httpx

    attrs = shot.get("attributes", {})
    image_asset = attrs.get("imageAsset") or {}
    template = image_asset.get("templateUrl")
    if not template:
        return None
    width = image_asset.get("width") or 1024
    height = image_asset.get("height") or 1024
    url = (
        template.replace("{w}", str(width))
        .replace("{h}", str(height))
        .replace("{f}", "png")
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return None
    return resp.content
