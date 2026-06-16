"""Shared price-preview computation for the REST router and MCP tools.

Both ``app.api.v1.pricing`` and ``app.mcp.tools.pricing`` build the same
suggested-price preview across three index branches (exchange_rate,
gdp_brackets, and the economic-index family ppp/bigmac/netflix/spotify).
This module is the single source of truth for:

  * ``finalize_price`` — the Decimal finalizer: VAT, then currency-aware
    charm rounding ("smart"/"99"/"95"/"none"), returned as a Decimal.
  * ``economic_index_suggested`` — the ProportionalCalculator-backed
    economic-index branch, in Decimal end-to-end.

Keeping these here prevents the two call sites from drifting (the float
``.99`` charm bug lived in exactly such a duplicated branch).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.economic_index import EconomicIndex
from app.schemas.pricing import (
    PricePreviewItem,
    PricePreviewRequest,
    PricePreviewSkippedItem,
)
from app.services.pricing.charming import apply_charming
from app.services.pricing.currency import effective_currency
from app.services.pricing.currency_rounding import (
    CURRENCY_PROFILES,
    DEFAULT_PROFILE,
    apply_currency_rounding,
)
from app.services.pricing.engine import CALCULATORS
from app.services.pricing.gdp_brackets import assign_tier
from app.services.pricing.vat import apply_vat
from app.services.rates import RateCacheClient, RateCacheError


def _to_decimal(value: Decimal | float | int | str) -> Decimal:
    """Coerce to Decimal via str() to avoid float binary-representation noise."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def finalize_price(
    raw: Decimal | float | int | str,
    charming_mode: str,
    currency_code: str,
) -> Decimal:
    """Apply currency-aware charm rounding to a raw price, in Decimal.

    Modes:
      * ``"smart"`` — :func:`apply_currency_rounding` (±10% nicest tier).
      * ``"99"`` / ``".99"`` / ``"95"`` / ``".95"`` — currency-aware charm.
        For 0-decimal (JPY/KRW) and 3-decimal (KWD/BHD) currencies a fixed
        2-decimal ``.99`` suffix is invalid, so we defer to
        :func:`apply_currency_rounding` (which knows each currency's
        decimals and charm pattern) rather than the 2-decimal-only
        :func:`apply_charming`.
      * ``"none"`` — quantize to the currency's decimal places, no charm.

    Always returns a Decimal; callers convert to float only at the
    response boundary.
    """
    raw_decimal = _to_decimal(raw)
    normalized = charming_mode.lstrip(".")

    if normalized == "smart":
        return apply_currency_rounding(raw_decimal, currency_code)

    profile = CURRENCY_PROFILES.get(currency_code, DEFAULT_PROFILE)

    if normalized in ("99", "95"):
        if profile.decimals == 2:
            # Standard 2-decimal currency: precise .99/.95 charm.
            return apply_charming(raw_decimal, normalized)
        # 0- or 3-decimal currency: a literal 2-decimal suffix is invalid,
        # so use the currency-aware nicest-tier rounding instead.
        return apply_currency_rounding(raw_decimal, currency_code)

    # "none": quantize to the currency's decimal places, no charm.
    quantum = Decimal(1).scaleb(-profile.decimals)
    return raw_decimal.quantize(quantum)


def economic_index_suggested(
    base_price: Decimal | float | int | str,
    territory_index: Decimal | float | int | str,
    base_index_value: Decimal | float | int | str,
    index_type: str,
    *,
    vat_rate: float | None,
    apply_vat_flag: bool,
    charming_mode: str,
    currency_code: str,
) -> Decimal:
    """Compute a suggested price for an economic-index territory, in Decimal.

    Routes through the ``ProportionalCalculator`` subclass registered for
    ``index_type`` (ppp/bigmac/netflix/spotify), applies VAT when requested,
    then the shared :func:`finalize_price` charm finalizer.
    """
    calculator_cls = CALCULATORS.get(index_type)
    if calculator_cls is None:
        raise ValueError(f"Unknown economic index type: {index_type}")

    raw = calculator_cls().calculate(
        _to_decimal(base_price),
        _to_decimal(territory_index),
        _to_decimal(base_index_value),
    )
    if apply_vat_flag and vat_rate and vat_rate > 0:
        raw = apply_vat(raw, vat_rate)
    return finalize_price(raw, charming_mode, currency_code)


# Result of a full preview run: the per-territory items plus any
# territories we had to skip (e.g. missing FX rate) so the UI can
# distinguish "no change" from "could not compute".
PreviewResult = tuple[list[PricePreviewItem], list[PricePreviewSkippedItem]]

# Callable that turns a (territory, currency, suggested) triple into a
# PricePreviewItem. Supplied by the caller because nearest-price-point
# matching + safety flagging lives next to the routers.
BuildItem = Callable[..., PricePreviewItem]
# Callable that raises the caller's error type (HTTPException / ToolError).
RaiseError = Callable[[str], Exception]


async def build_preview_items(
    *,
    body: PricePreviewRequest,
    session: AsyncSession,
    territory_map: dict[str, Any],
    all_territories: list[Any],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    build_item: BuildItem,
    raise_error: RaiseError,
) -> PreviewResult:
    """Compute preview items for any index_type, in Decimal throughout.

    Shared by the subscription/IAP REST endpoints and the MCP tools so the
    three branches (exchange_rate, gdp_brackets, economic-index) live in
    exactly one place. ``build_item`` builds a ``PricePreviewItem`` from a
    (territory, currency, suggested) triple; ``raise_error`` raises the
    caller's error type for invalid requests / upstream failures.
    """
    if body.index_type == "exchange_rate":
        return await _exchange_rate_items(
            body=body,
            territory_map=territory_map,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
            build_item=build_item,
            raise_error=raise_error,
        )
    if body.index_type == "gdp_brackets":
        return await _gdp_bracket_items(
            body=body,
            session=session,
            all_territories=all_territories,
            price_points_by_territory=price_points_by_territory,
            current_price_by_territory=current_price_by_territory,
            build_item=build_item,
            raise_error=raise_error,
        )
    return await _economic_index_items(
        body=body,
        session=session,
        territory_map=territory_map,
        all_territories=all_territories,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        build_item=build_item,
        raise_error=raise_error,
    )


async def _fetch_rates(base: str, raise_error: RaiseError) -> dict[str, float]:
    try:
        rate_client = RateCacheClient(settings.RATE_CACHE_API_URL)
        return await rate_client.get_rates(base=base)
    except RateCacheError as exc:
        raise raise_error(f"Failed to fetch exchange rates: {exc}")


def _skipped(territory: Any, reason: str) -> PricePreviewSkippedItem:
    return PricePreviewSkippedItem(
        territory_code=territory.code,
        territory_name=territory.name,
        reason=reason,
    )


# Callable returning the pre-FX base price (in the base currency) for a
# territory. The two FX branches differ only in this function:
# exchange_rate uses a flat base price, gdp_brackets uses the territory's
# GDP-tier price.
BasePriceFor = Callable[[Any], Decimal]


async def _fx_rate_items(
    *,
    body: PricePreviewRequest,
    base_currency: str,
    base_price_for: BasePriceFor,
    all_territories: list[Any],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    build_item: BuildItem,
    raise_error: RaiseError,
) -> PreviewResult:
    """Shared core for the FX-converted branches (exchange_rate, gdp_brackets).

    Converts each territory's base price into local currency at the cached
    FX rate, applies VAT + charm, and emits an item. Territories with no
    rate are reported as skipped (``missing_fx_rate``) so the UI can tell
    "could not compute" apart from "no change needed".
    """
    rates = await _fetch_rates(base_currency, raise_error)

    items: list[PricePreviewItem] = []
    skipped: list[PricePreviewSkippedItem] = []
    for territory in all_territories:
        currency = effective_currency(
            territory, price_points_by_territory.get(territory.code),
        )
        if currency == base_currency:
            rate: float | None = 1.0
        else:
            rate = rates.get(currency)
        if rate is None:
            skipped.append(_skipped(territory, "missing_fx_rate"))
            continue

        # Decimal end-to-end; the float() cast is the response boundary.
        raw = base_price_for(territory) * _to_decimal(rate)
        if body.apply_vat and territory.vat_rate and territory.vat_rate > 0:
            raw = apply_vat(raw, territory.vat_rate)
        suggested = float(finalize_price(raw, body.charming_mode, currency))
        items.append(_emit(
            territory, currency, suggested,
            current_price_by_territory, price_points_by_territory, build_item,
        ))
    return items, skipped


def _emit(
    territory: Any,
    currency: str,
    suggested: float,
    current_price_by_territory: dict[int, Any],
    price_points_by_territory: dict[str, list[dict]],
    build_item: BuildItem,
) -> PricePreviewItem:
    """Build one preview item via the caller's nearest-match builder."""
    return build_item(
        territory=territory,
        currency_code=currency,
        suggested=suggested,
        current_price_by_territory=current_price_by_territory,
        price_points_by_territory=price_points_by_territory,
    )


async def _exchange_rate_items(
    *,
    body: PricePreviewRequest,
    territory_map: dict[str, Any],
    all_territories: list[Any],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    build_item: BuildItem,
    raise_error: RaiseError,
) -> PreviewResult:
    base_territory = territory_map.get(body.base_territory_code)
    if base_territory is None:
        raise raise_error(
            f"Base territory '{body.base_territory_code}' not found"
        )
    base_currency = effective_currency(
        base_territory, price_points_by_territory.get(base_territory.code),
    )
    base_price = _to_decimal(body.base_price)
    return await _fx_rate_items(
        body=body,
        base_currency=base_currency,
        base_price_for=lambda _territory: base_price,
        all_territories=all_territories,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        build_item=build_item,
        raise_error=raise_error,
    )


async def _gdp_bracket_items(
    *,
    body: PricePreviewRequest,
    session: AsyncSession,
    all_territories: list[Any],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    build_item: BuildItem,
    raise_error: RaiseError,
) -> PreviewResult:
    assert body.gdp_config is not None  # validator enforced
    gdp_config = body.gdp_config
    gdp_result = await session.execute(
        select(EconomicIndex).where(
            EconomicIndex.index_type == "gdp_per_capita_ppp"
        )
    )
    gdp_by_territory_id: dict[int, float] = {
        idx.territory_id: idx.value for idx in gdp_result.scalars().all()
    }

    def tier_price_for(territory: Any) -> Decimal:
        tier = assign_tier(
            territory.code, gdp_by_territory_id.get(territory.id), gdp_config,
        )
        return _to_decimal(gdp_config.tier_prices_usd[tier])

    return await _fx_rate_items(
        body=body,
        base_currency="USD",
        base_price_for=tier_price_for,
        all_territories=all_territories,
        price_points_by_territory=price_points_by_territory,
        current_price_by_territory=current_price_by_territory,
        build_item=build_item,
        raise_error=raise_error,
    )


async def _economic_index_items(
    *,
    body: PricePreviewRequest,
    session: AsyncSession,
    territory_map: dict[str, Any],
    all_territories: list[Any],
    price_points_by_territory: dict[str, list[dict]],
    current_price_by_territory: dict[int, Any],
    build_item: BuildItem,
    raise_error: RaiseError,
) -> PreviewResult:
    indices_result = await session.execute(
        select(EconomicIndex).where(
            EconomicIndex.index_type == body.index_type
        )
    )
    index_by_territory: dict[int, float] = {
        idx.territory_id: idx.value for idx in indices_result.scalars().all()
    }
    base_territory = territory_map.get(body.base_territory_code)
    if base_territory is None:
        raise raise_error(
            f"Base territory '{body.base_territory_code}' not found"
        )
    base_index_value = index_by_territory.get(base_territory.id)
    if base_index_value is None or base_index_value == 0:
        raise raise_error(
            f"No {body.index_type} index data for territory "
            f"'{body.base_territory_code}'"
        )

    # Unlike the FX branches, a territory with no index value is silently
    # omitted rather than reported as skipped (there is nothing to convert,
    # not a transient lookup failure). VAT + charm happen inside
    # ``economic_index_suggested``.
    items: list[PricePreviewItem] = []
    for territory in all_territories:
        territory_index = index_by_territory.get(territory.id)
        if territory_index is None:
            continue
        currency = effective_currency(
            territory, price_points_by_territory.get(territory.code),
        )
        suggested = float(economic_index_suggested(
            base_price=body.base_price,
            territory_index=territory_index,
            base_index_value=base_index_value,
            index_type=body.index_type,
            vat_rate=territory.vat_rate,
            apply_vat_flag=body.apply_vat,
            charming_mode=body.charming_mode,
            currency_code=currency,
        ))
        items.append(_emit(
            territory, currency, suggested,
            current_price_by_territory, price_points_by_territory, build_item,
        ))
    return items, []
