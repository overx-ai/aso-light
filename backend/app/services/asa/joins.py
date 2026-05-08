"""Service-layer join + insight queries between ASA data and our tracked keywords.

These are read-only over the local DB; no Apple network calls. They are
designed to be cheap enough to power a UI table or an MCP tool response
in real time without an explicit cache layer.

Note: the local schema stores the keyword text on `Keyword.text` and the
organic rank on `KeywordRanking.rank` (most recent recorded_at), with
`KeywordTracking` acting as the join row between an `App` and a
`Keyword`. The local-app ↔ ASA fact-table link goes through
`App.asc_app_id`, which equals Apple's adam_id and is the same string
denormalized into `ASAMetricDaily.app_adam_id`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import ASAKeyword, ASAMetricDaily, ASASearchTerm
from app.models.keyword import Keyword, KeywordRanking, KeywordTracking


def _latest_rank_subquery():
    """Per-tracking latest rank — max(recorded_at) from KeywordRanking."""
    latest_at = (
        select(
            KeywordRanking.tracking_id.label("tid"),
            func.max(KeywordRanking.recorded_at).label("max_at"),
        )
        .group_by(KeywordRanking.tracking_id)
        .subquery()
    )
    return (
        select(
            KeywordRanking.tracking_id.label("tracking_id"),
            KeywordRanking.rank.label("rank"),
        )
        .join(
            latest_at,
            (KeywordRanking.tracking_id == latest_at.c.tid)
            & (KeywordRanking.recorded_at == latest_at.c.max_at),
        )
        .subquery()
    )


async def paid_organic_join(
    *, session: AsyncSession, app_id: int, days: int = 30,
) -> list[dict[str, Any]]:
    """Join tracked-organic keywords against ASA keyword metrics over a window.

    Output shape matches `app.schemas.asa.PaidOrganicJoinRow`. Match is
    case-insensitive on text. Tracked terms with no matching ASA keyword
    return zeros in the paid_*_30d columns and currency=None.
    """
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = date.today() - timedelta(days=days)

    metrics_subq = (
        select(
            func.lower(ASAKeyword.text).label("text_lower"),
            func.coalesce(func.sum(ASAMetricDaily.impressions), 0).label("imp"),
            func.coalesce(func.sum(ASAMetricDaily.taps), 0).label("taps"),
            func.coalesce(func.sum(ASAMetricDaily.installs), 0).label("ins"),
            func.coalesce(func.sum(ASAMetricDaily.spend_amount), 0).label("spend"),
            func.max(ASAMetricDaily.spend_currency).label("currency"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "KEYWORD")
            & (ASAMetricDaily.dim_id == ASAKeyword.id)
            & (ASAMetricDaily.date >= cutoff)
            & (ASAMetricDaily.app_adam_id == app.asc_app_id),
            isouter=True,
        )
        .where(ASAKeyword.archived_at.is_(None))
        .group_by(func.lower(ASAKeyword.text))
        .subquery()
    )

    latest_rank = _latest_rank_subquery()

    stmt = (
        select(
            Keyword.text.label("term"),
            latest_rank.c.rank.label("rank"),
            metrics_subq.c.imp,
            metrics_subq.c.taps,
            metrics_subq.c.ins,
            metrics_subq.c.spend,
            metrics_subq.c.currency,
        )
        .select_from(KeywordTracking)
        .join(Keyword, Keyword.id == KeywordTracking.keyword_id)
        .join(
            latest_rank,
            latest_rank.c.tracking_id == KeywordTracking.id,
            isouter=True,
        )
        .join(
            metrics_subq,
            func.lower(Keyword.text) == metrics_subq.c.text_lower,
            isouter=True,
        )
        .where(KeywordTracking.app_id == app_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "term": r.term,
            "organic_rank": r.rank,
            "paid_impressions_30d": int(r.imp or 0),
            "paid_taps_30d": int(r.taps or 0),
            "paid_installs_30d": int(r.ins or 0),
            "paid_spend_30d": Decimal(str(r.spend or 0)),
            "paid_spend_currency": r.currency,
        }
        for r in rows
    ]


async def suggest_organic_keywords_to_track(
    *, session: AsyncSession, app_id: int, days: int = 30,
    min_taps: int = 20,
) -> list[dict[str, Any]]:
    """Search terms with >= min_taps not already in tracked organic keywords.

    Surfaces ASA-driven discovery: terms that converted in paid that we
    aren't yet measuring in the organic keyword tracker.
    """
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(
            ASASearchTerm.text,
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("installs"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff)
            & (ASAMetricDaily.app_adam_id == app.asc_app_id),
        )
        .group_by(ASASearchTerm.text)
        .having(func.sum(ASAMetricDaily.taps) >= min_taps)
    )
    rows = (await session.execute(stmt)).all()
    tracked_rows = (
        await session.execute(
            select(Keyword.text)
            .join(KeywordTracking, KeywordTracking.keyword_id == Keyword.id)
            .where(KeywordTracking.app_id == app_id)
        )
    ).all()
    tracked = {r[0].lower() for r in tracked_rows}
    return [
        {"text": r.text, "taps": int(r.taps), "installs": int(r.installs)}
        for r in rows
        if r.text.lower() not in tracked
    ]


async def suggest_negative_candidates(
    *, session: AsyncSession, app_id: int, days: int = 30,
    min_spend: float = 10.0, max_conv_rate: float = 0.005,
) -> list[dict[str, Any]]:
    """Search terms wasting spend with low conversion (negative-keyword candidates).

    `min_spend` and `max_conv_rate` are float for ergonomic API surfaces;
    internally we compare against the Numeric/Decimal columns. The
    conversion rate is computed as installs/taps in the result.
    """
    app = (await session.execute(select(App).where(App.id == app_id))).scalar_one()
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(
            ASASearchTerm.id,
            ASASearchTerm.text,
            ASASearchTerm.ad_group_id,
            func.sum(ASAMetricDaily.spend_amount).label("spend"),
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("installs"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff)
            & (ASAMetricDaily.app_adam_id == app.asc_app_id),
        )
        .group_by(ASASearchTerm.id, ASASearchTerm.text, ASASearchTerm.ad_group_id)
        .having(func.sum(ASAMetricDaily.spend_amount) >= min_spend)
    )
    out: list[dict[str, Any]] = []
    for r in (await session.execute(stmt)).all():
        taps = int(r.taps or 0)
        installs = int(r.installs or 0)
        conv = installs / taps if taps else 0.0
        if conv <= max_conv_rate:
            out.append({
                "search_term_id": r.id,
                "text": r.text,
                "ad_group_id": r.ad_group_id,
                "spend": float(r.spend),
                "taps": taps,
                "installs": installs,
                "conversion_rate": conv,
            })
    return out
