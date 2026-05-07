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

import asyncio
import base64
import json
import logging
import re
from datetime import date
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


_DOT_VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.v(?P<n>\d+)$")
_UNDERSCORE_VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+?)_v(?P<n>\d+)$")


def next_versioned_product_id(product_id: str) -> str:
    """Return the next version suffix for a productId, bumping in place.

    Recognized suffix styles:

    * ``.v{n}``  -> ``.v{n+1}``     e.g. ``app.pro.v2``  -> ``app.pro.v3``
    * ``_v{n}``  -> ``_v{n+1}``     e.g. ``app.pro_v2``  -> ``app.pro_v3``
    * no suffix  -> ``.v2``         e.g. ``app.pro``     -> ``app.pro.v2``

    Default for a fresh bump is ``.v2`` (dot-style). Pre-existing
    ``_v{n}`` ids keep bumping as ``_v{n+1}`` so we never mix styles
    within a single productId lineage.
    """
    match = _DOT_VERSION_SUFFIX_RE.match(product_id)
    if match is not None:
        return f"{match.group('base')}.v{int(match.group('n')) + 1}"
    match = _UNDERSCORE_VERSION_SUFFIX_RE.match(product_id)
    if match is not None:
        return f"{match.group('base')}_v{int(match.group('n')) + 1}"
    return f"{product_id}.v2"


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


def _decode_offer_id(offer_id: str) -> dict[str, Any]:
    """Decode Apple's opaque introductory-offer ID.

    Apple does NOT populate ``relationships.territory`` or
    ``relationships.subscriptionPricePoint`` on most intro-offer
    list responses, but encodes both into the offer's id as a
    base64-padded JSON blob: ``{"s":sub,"d":epoch,"i":alpha2,"t":tier,"p":price}``.
    Returns ``{}`` on any decode failure (caller decides how to react).
    """
    try:
        padded = offer_id + "=" * (-len(offer_id) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def _sanitize_offer_date(value: str | None) -> str | None:
    """Return ``value`` only if it parses to today or a future date.

    Apple rejects past startDate/endDate on intro offers and tells the
    caller to pass ``null`` for "effective immediately". Source offers
    cloned from a long-running sub usually have past dates.
    """
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    if parsed < date.today():
        return None
    return parsed.isoformat()


async def _find_target_price_point_id(
    pricing: ASCPricingService,
    target_asc_sub_id: str,
    territory_alpha3: str,
    target_customer_price: float,
) -> str | None:
    """Look up the new sub's ``price_point_id`` for one (territory, price).

    Apple's price-point IDs encode the *source* sub, so cloned IDs are
    invalid on the target. We resolve the target's equivalent by listing
    its price points filtered by territory and matching customer price.
    Returns ``None`` when no point in the target sub lands within
    half-a-cent of the source's customer price.
    """
    points = await pricing.get_price_points(
        target_asc_sub_id, territory_code=territory_alpha3,
    )
    target = float(target_customer_price)
    for p in points:
        if abs(float(p.get("customer_price", 0)) - target) < 0.005:
            return p.get("price_point_id")
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
            # ASC requires the subscription reference name to be unique
            # within an app, and the source sub still holds its old name
            # at create-time. Default to the bumped productId (always
            # globally unique) when the caller didn't override.
            effective_name = new_name or new_product_id
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
                    name=effective_name,
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
                name=effective_name,
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
        # Apple price-point IDs encode the source sub, so we re-resolve
        # each (territory, customer_price) against the new sub's points.
        # Idempotent: skip territories that already have a price on the
        # target. Apple's price endpoint occasionally returns 5xx; one
        # retry per territory clears most of those.
        if scope.get("price_schedule", True):
            res = await self.session.execute(
                select(SubscriptionPrice, Territory)
                .join(Territory, Territory.id == SubscriptionPrice.territory_id)
                .where(SubscriptionPrice.subscription_id == source.id)
            )
            source_rows = res.all()
            already_priced_alpha3: set[str] = set()
            try:
                existing = await self.pricing.get_subscription_prices(
                    new_asc_id,
                )
                for ep in existing:
                    code = ep.get("territory_code")
                    if code:
                        already_priced_alpha3.add(code)
            except ASCAPIError as exc:
                # Probe failure is non-fatal — we just won't be able to
                # skip already-priced territories on this re-run.
                logger.debug(
                    "prices: failed to probe existing prices for %s: %s",
                    new_asc_id, exc,
                )
            steps.append(_step(
                "prices", "running",
                completed=0, total=len(source_rows),
            ))
            for sp, territory in source_rows:
                if sp.price_point_id is None:
                    steps[-1]["completed"] += 1
                    continue
                alpha3 = ALPHA2_TO_ALPHA3.get(territory.code)
                if alpha3 is None:
                    errors.append(
                        f"prices[{territory.code}]: no alpha-3 mapping"
                    )
                    continue
                if alpha3 in already_priced_alpha3:
                    steps[-1]["completed"] += 1
                    continue
                try:
                    target_pp = await _find_target_price_point_id(
                        self.pricing, new_asc_id, alpha3, sp.customer_price,
                    )
                    if target_pp is None:
                        errors.append(
                            f"prices[{territory.code}]: no price point for "
                            f"{sp.customer_price} on target sub"
                        )
                        continue
                    last_exc: ASCAPIError | None = None
                    for attempt in range(2):
                        try:
                            await self.pricing.create_subscription_price(
                                subscription_id=new_asc_id,
                                price_point_id=target_pp,
                            )
                            last_exc = None
                            break
                        except ASCAPIError as exc:
                            last_exc = exc
                            if exc.status_code < 500 or attempt == 1:
                                break
                            await asyncio.sleep(1.0)
                    if last_exc is not None:
                        errors.append(
                            f"prices[{territory.code}]: {last_exc}"
                        )
                        continue
                    steps[-1]["completed"] += 1
                except ASCAPIError as exc:
                    errors.append(f"prices[{territory.code}]: {exc}")
            steps[-1]["status"] = (
                "done"
                if steps[-1]["completed"] == steps[-1]["total"]
                else "partial"
            )

        # Step 6: intro offers
        # Same price-point remap as Step 5. Source startDate is often
        # in the past on long-running subs — Apple rejects past dates,
        # so we null those out (Apple treats null as "effective now").
        # Idempotent: skip alpha-2 territories that already have an
        # intro offer of the same mode on the target sub.
        if scope.get("intro_offers", True):
            try:
                offers = await (
                    self.pricing.list_subscription_introductory_offers(
                        source.asc_subscription_id,
                    )
                )
                target_offer_keys: set[tuple[str, str]] = set()
                try:
                    target_offers = await (
                        self.pricing
                        .list_subscription_introductory_offers(new_asc_id)
                    )
                    for to in target_offers:
                        td = _decode_offer_id(to["resource"].get("id", ""))
                        ta2 = td.get("i")
                        tmode = (
                            to["resource"].get("attributes", {})
                            .get("offerMode")
                        )
                        if ta2 and tmode:
                            target_offer_keys.add((ta2, tmode))
                except ASCAPIError as exc:
                    # Probe failure is non-fatal — duplicates that already
                    # exist on the target will surface as 409 below.
                    logger.debug(
                        "intro_offers: failed to probe existing offers "
                        "for %s: %s",
                        new_asc_id, exc,
                    )
                steps.append(_step(
                    "intro_offers", "running",
                    completed=0, total=len(offers),
                ))
                for entry in offers:
                    res = entry["resource"]
                    a = res.get("attributes", {})
                    offer_mode = a.get("offerMode")

                    # Apple omits relationships on most intro-offer list
                    # responses; the id itself encodes the territory
                    # (alpha-2) and the source customer price.
                    decoded = _decode_offer_id(res.get("id", ""))
                    territory_alpha2 = decoded.get("i")
                    territory_alpha3 = (
                        ALPHA2_TO_ALPHA3.get(territory_alpha2)
                        if territory_alpha2 else None
                    )
                    try:
                        source_price = float(decoded.get("p") or 0)
                    except (TypeError, ValueError):
                        source_price = 0.0

                    if (
                        territory_alpha2
                        and (territory_alpha2, offer_mode) in target_offer_keys
                    ):
                        steps[-1]["completed"] += 1
                        continue

                    target_pp_id: str | None = None
                    if offer_mode != "FREE_TRIAL":
                        # Paid offers need a price point on the new sub.
                        if not territory_alpha3 or source_price <= 0:
                            errors.append(
                                "intro_offers: missing territory or price "
                                "on source"
                            )
                            continue
                        try:
                            target_pp_id = await _find_target_price_point_id(
                                self.pricing,
                                new_asc_id,
                                territory_alpha3,
                                source_price,
                            )
                        except ASCAPIError as exc:
                            errors.append(
                                f"intro_offers[{territory_alpha3}]: {exc}"
                            )
                            continue
                        if target_pp_id is None:
                            errors.append(
                                f"intro_offers[{territory_alpha3}]: no price "
                                f"point for {source_price} on target sub"
                            )
                            continue

                    try:
                        await (
                            self.pricing
                            .create_subscription_introductory_offer(
                                subscription_id=new_asc_id,
                                offer_mode=offer_mode,
                                duration=a.get("duration"),
                                number_of_periods=int(
                                    a.get("numberOfPeriods", 1),
                                ),
                                territory_id=territory_alpha3,
                                price_point_id=target_pp_id,
                                start_date=_sanitize_offer_date(
                                    a.get("startDate"),
                                ),
                                end_date=_sanitize_offer_date(
                                    a.get("endDate"),
                                ),
                            )
                        )
                        steps[-1]["completed"] += 1
                    except ASCAPIError as exc:
                        errors.append(
                            f"intro_offers[{territory_alpha3 or '?'}]: {exc}"
                        )
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
        # Apple's screenshot upload occasionally returns a generic 5xx;
        # one retry after a short backoff usually clears it.
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
                        # Always derive a clean filename from the new
                        # productId. Apple's reserve endpoint 500s on
                        # filenames without an extension (legacy review
                        # screenshots are sometimes stored as just
                        # "SOURCE"), so don't trust the source's value.
                        file_name = (
                            f"{new_product_id.replace('.', '_')}.png"
                        )
                        last_exc: ASCAPIError | None = None
                        for attempt in range(2):
                            try:
                                await (
                                    self.pricing
                                    .upload_subscription_review_screenshot(
                                        subscription_id=new_asc_id,
                                        file_name=file_name,
                                        file_bytes=file_bytes,
                                    )
                                )
                                last_exc = None
                                break
                            except ASCAPIError as exc:
                                last_exc = exc
                                if exc.status_code < 500 or attempt == 1:
                                    break
                                await asyncio.sleep(2.0)
                        if last_exc is not None:
                            raise last_exc
                        steps[-1] = _step(
                            "screenshot", "done",
                            detail=f"{len(file_bytes)} bytes",
                        )
            except ASCAPIError as exc:
                steps[-1] = _step("screenshot", "failed", detail=str(exc))
                errors.append(f"screenshot: {exc}")

        # Step 8: archive source by removing all territories
        # ``subscriptionAvailabilities`` does not allow PATCH — Apple only
        # accepts CREATE / GET_INSTANCE. Posting a fresh record with an
        # empty territory list replaces the existing availability,
        # taking the source off sale (existing subscribers keep access).
        if scope.get("auto_archive", True):
            steps.append(_step("archive_source", "running"))
            try:
                await self.pricing.create_subscription_availability(
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
            # ASC requires the IAP reference name unique per app — same
            # rule as subscriptions. Default to the bumped productId.
            effective_name = new_name or new_product_id
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
                    name=effective_name,
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
                name=effective_name,
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
                        # Always derive a clean filename from the new
                        # productId. Apple's reserve endpoint 500s on
                        # filenames without an extension (legacy review
                        # screenshots are sometimes stored as just
                        # "SOURCE"), so don't trust the source's value.
                        file_name = (
                            f"{new_product_id.replace('.', '_')}.png"
                        )
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
