"""Shared, tenant-scoped ASA analytics queries.

Single source of truth for the read-only metric rollups served by both the
REST surface (``app.api.v1.asa_app``) and the MCP surface
(``app.mcp.tools.asa``). Centralizing them here means the cross-tenant
scoping (C1), Decimal money handling (C2), storefront normalization (I6),
multi-currency grouping (I3/I4) and the inclusive-window math (M1) live in
exactly one place.

Scoping invariant: every query that touches ``ASAMetricDaily`` filters on
``credential_id IN (credentials owned by user_id)``. A row whose
``credential_id`` is NULL is invisible to everyone — the scope fails closed.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.territories import ALPHA2_TO_ALPHA3
from app.models.asa import ASACredential, ASAMetricDaily, ASASearchTerm


def owned_credential_ids(user_id: int):
    """Scalar subquery of ``asa_credentials.id`` owned by ``user_id``.

    Used to scope every ``ASAMetricDaily`` read to the caller's own data so
    two tenants advertising the same Apple app never see each other's metrics.
    """
    return (
        select(ASACredential.id)
        .where(ASACredential.user_id == user_id)
        .scalar_subquery()
    )


def window_cutoff(days: int) -> date:
    """Inclusive lower bound for a ``days``-long window ending today.

    ``days=30`` yields exactly 30 calendar days (today inclusive), not 31.
    """
    return date.today() - timedelta(days=days - 1)


def normalize_storefront(storefront: str) -> str:
    """Map an inbound alpha-2 (or alpha-3) storefront code to alpha-3.

    Stored ``ASAMetricDaily.storefront`` values are alpha-3 (Apple's
    ``countryOrRegion``); callers may pass alpha-2. Unknown codes pass through
    uppercased so an already-alpha-3 value still matches.
    """
    code = storefront.upper()
    return ALPHA2_TO_ALPHA3.get(code, code)


def _scoped_metric_join_clause(user_id: int, dim_kind: str, cutoff: date):
    """Common ON clause binding ``ASAMetricDaily`` to a search-term dim, scoped."""
    return (
        (ASAMetricDaily.dim_kind == dim_kind)
        & (ASAMetricDaily.dim_id == ASASearchTerm.id)
        & (ASAMetricDaily.date >= cutoff)
        & (ASAMetricDaily.credential_id.in_(owned_credential_ids(user_id)))
    )


async def performance_rows(
    *,
    session: AsyncSession,
    user_id: int,
    app_adam_id: str,
    grain: str,
    days: int,
    storefront: str | None = None,
) -> tuple[date, list[ASAMetricDaily]]:
    """Raw daily metric rows for one app at one grain over a window.

    Returns ``(cutoff, rows)`` where ``rows`` are full ``ASAMetricDaily`` ORM
    objects ordered newest-first. The caller rolls these up; the row shape is
    deliberately preserved so the frontend trend chart keeps working.
    """
    cutoff = window_cutoff(days)
    stmt = (
        select(ASAMetricDaily)
        .where(
            ASAMetricDaily.app_adam_id == app_adam_id,
            ASAMetricDaily.dim_kind == grain,
            ASAMetricDaily.date >= cutoff,
            ASAMetricDaily.credential_id.in_(owned_credential_ids(user_id)),
        )
        .order_by(ASAMetricDaily.date.desc())
    )
    if storefront:
        stmt = stmt.where(
            ASAMetricDaily.storefront == normalize_storefront(storefront)
        )
    rows = (await session.execute(stmt)).scalars().all()
    return cutoff, list(rows)


def _search_term_report_stmt(
    *,
    user_id: int,
    cutoff: date,
    ad_group_id: int | None,
    min_impressions: int | None,
) -> Select:
    """Search-term rollup, grouped by currency so each row is single-currency."""
    stmt = (
        select(
            ASASearchTerm.id,
            ASASearchTerm.text,
            ASASearchTerm.match_type,
            ASASearchTerm.ad_group_id,
            func.sum(ASAMetricDaily.impressions).label("imp"),
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("ins"),
            func.sum(ASAMetricDaily.spend_amount).label("spend"),
            ASAMetricDaily.spend_currency.label("currency"),
        )
        .join(
            ASAMetricDaily,
            _scoped_metric_join_clause(user_id, "SEARCH_TERM", cutoff),
        )
        .group_by(
            ASASearchTerm.id,
            ASASearchTerm.text,
            ASASearchTerm.match_type,
            ASASearchTerm.ad_group_id,
            ASAMetricDaily.spend_currency,
        )
    )
    if ad_group_id is not None:
        stmt = stmt.where(ASASearchTerm.ad_group_id == ad_group_id)
    if min_impressions is not None:
        stmt = stmt.having(func.sum(ASAMetricDaily.impressions) >= min_impressions)
    return stmt


async def search_term_report_rows(
    *,
    session: AsyncSession,
    user_id: int,
    days: int,
    ad_group_id: int | None = None,
    min_impressions: int | None = None,
) -> tuple[date, list[dict[str, Any]]]:
    """Search-term performance rollup over a window.

    Returns ``(cutoff, rows)``. Each row is single-currency (the query groups
    by ``spend_currency``), so ``spend`` is a clean per-currency Decimal — a
    term advertised in two currencies yields two rows.
    """
    cutoff = window_cutoff(days)
    stmt = _search_term_report_stmt(
        user_id=user_id,
        cutoff=cutoff,
        ad_group_id=ad_group_id,
        min_impressions=min_impressions,
    )
    rows = (await session.execute(stmt)).all()
    return cutoff, [
        {
            "search_term_id": r.id,
            "text": r.text,
            "match_type": r.match_type,
            "ad_group_id": r.ad_group_id,
            "impressions": int(r.imp or 0),
            "taps": int(r.taps or 0),
            "installs": int(r.ins or 0),
            "spend": Decimal(str(r.spend or 0)),
            "spend_currency": r.currency,
        }
        for r in rows
    ]
