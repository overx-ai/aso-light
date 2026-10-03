"""Saved price versions: every apply (and every baseline) of one product.

Shared by the REST and MCP apply paths so a version looks the same however
the prices were sent.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.iap import IAPPrice
from app.models.price_version import PriceVersion
from app.models.subscription import SubscriptionPrice
from app.models.territory import Territory
from app.schemas.pricing import (
    PriceApplyRequest,
    PriceApplyResponse,
    ProductKind,
)

logger = logging.getLogger(__name__)

CachedPrice = SubscriptionPrice | IAPPrice

# Two applies to one product can read the same max(version); the loser of
# the unique constraint re-reads and takes the next number.
VERSION_INSERT_ATTEMPTS = 3


async def record_price_version(
    session: AsyncSession,
    *,
    user_id: int,
    app_id: int,
    product_kind: ProductKind,
    product_ref_id: int,
    product_id: str,
    source: str,
    current_prices: Sequence[CachedPrice],
    territory_by_id: dict[int, Territory],
    body: PriceApplyRequest | None = None,
    response: PriceApplyResponse | None = None,
    resolved: dict[str, float] | None = None,
    applied: Collection[str] = (),
    kept_current_price: Collection[str] = (),
    submitted: Sequence[dict[str, str]] = (),
    base_territory_code: str | None = None,
    note: str | None = None,
) -> PriceVersion:
    """Save one version. ``body is None`` records the cached prices as-is.

    ``resolved`` maps territory → customer price of the requested price point;
    ``applied`` holds the territories Apple accepted, ``kept_current_price``
    those of them sent with ``preserveCurrentPrice``; ``submitted`` is the IAP
    schedule sent to Apple, whose entries outside ``body.items`` are the
    preserved territories.
    """
    previous = {
        territory_by_id[p.territory_id].code: p
        for p in current_prices if p.territory_id in territory_by_id
    }
    currency = {t.code: t.currency_code for t in territory_by_id.values()}

    def previous_price(code: str) -> float | None:
        prev = previous.get(code)
        return prev.customer_price if prev else None

    def entry(code: str, price_point_id: str | None, price: float | None,
              origin: str, force: bool = False,
              preserve_current_price: bool = False) -> dict[str, Any]:
        prev = previous.get(code)
        return {
            "territory_code": code,
            "currency": currency.get(code),
            "customer_price": price,
            "price_point_id": price_point_id,
            "previous_customer_price": previous_price(code),
            "previous_price_point_id": prev.price_point_id if prev else None,
            "force": force,
            "preserve_current_price": preserve_current_price,
            "origin": origin,
        }

    if body is None:
        items = [
            entry(code, p.price_point_id, p.customer_price, "current")
            for code, p in sorted(previous.items())
        ]
    else:
        resolved = resolved or {}
        skipped = {s.territory_code for s in response.skipped_items} if response else set()
        requested = {i.territory_code for i in body.items}

        def requested_origin(code: str) -> str:
            if code in skipped:
                return "skipped"
            if code in applied:
                return "applied"
            return "failed"

        items = [
            entry(
                i.territory_code, i.price_point_id,
                resolved.get(i.territory_code),
                requested_origin(i.territory_code),
                force=i.force,
                preserve_current_price=i.territory_code in kept_current_price,
            )
            for i in body.items
        ]
        items += [
            entry(
                e["territory_code"], e["price_point_id"],
                previous_price(e["territory_code"]),
                "preserved",
            )
            for e in submitted if e["territory_code"] not in requested
        ]

    fields: dict[str, Any] = {
        "user_id": user_id,
        "app_id": app_id,
        "product_kind": product_kind,
        "product_ref_id": product_ref_id,
        "product_id": product_id,
        "source": source,
        "config": (
            body.source_config.model_dump(mode="json")
            if body and body.source_config else None
        ),
        "base_territory_code": base_territory_code,
        "intro_offer": (
            body.intro_offer.model_dump(mode="json")
            if body and body.intro_offer else None
        ),
        "items": items,
        "result": response.model_dump(mode="json") if response else None,
        "note": note if note is not None else (body.note if body else None),
    }
    for attempt in range(1, VERSION_INSERT_ATTEMPTS + 1):
        row = PriceVersion(
            version=await _next_version(
                session, app_id, product_kind, product_ref_id,
            ),
            **fields,
        )
        session.add(row)
        # Committing here (the REST and MCP sessions also commit on exit)
        # keeps the version durable the moment it is numbered.
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if attempt == VERSION_INSERT_ATTEMPTS:
                raise
    await session.refresh(row)
    return row


async def record_apply_version(
    session: AsyncSession, **kwargs: Any,
) -> PriceVersion | None:
    """:func:`record_price_version` for an apply that already reached Apple.

    Apple has changed by now, so a failed save is logged and swallowed:
    turning it into an error would report a successful apply as failed.
    """
    try:
        return await record_price_version(session, **kwargs)
    except SQLAlchemyError:
        await session.rollback()
        logger.exception(
            "Price version NOT saved for %s %s (app %s) after an apply",
            kwargs.get("product_kind"), kwargs.get("product_ref_id"),
            kwargs.get("app_id"),
        )
        return None


async def _next_version(
    session: AsyncSession,
    app_id: int,
    product_kind: ProductKind,
    product_ref_id: int,
) -> int:
    latest = await session.scalar(
        select(func.max(PriceVersion.version)).where(
            PriceVersion.app_id == app_id,
            PriceVersion.product_kind == product_kind,
            PriceVersion.product_ref_id == product_ref_id,
        )
    )
    return (latest or 0) + 1


async def list_versions(
    session: AsyncSession,
    *,
    app_id: int,
    product_kind: ProductKind,
    product_ref_id: int,
) -> list[PriceVersion]:
    result = await session.execute(
        select(PriceVersion)
        .where(
            PriceVersion.app_id == app_id,
            PriceVersion.product_kind == product_kind,
            PriceVersion.product_ref_id == product_ref_id,
        )
        .order_by(PriceVersion.version.desc())
    )
    return list(result.scalars().all())


async def get_version(
    session: AsyncSession, *, app_id: int, version_id: int,
) -> PriceVersion | None:
    return await session.scalar(
        select(PriceVersion).where(
            PriceVersion.id == version_id, PriceVersion.app_id == app_id,
        )
    )
