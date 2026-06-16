"""Path B: derive volume + difficulty signals from existing ASA search-term data.

We already store search terms (``ASASearchTerm``) and per-day metrics
(``ASAMetricDaily`` with ``dim_kind="SEARCH_TERM"``). For every term we've
spent ad money on, that's a per-storefront daily impression series. From it
we synthesize:

* ``volume_score`` — log-scaled normalization of avg daily impressions across
  the lookback window. 0 (no impressions) … 100 (top observed term).
* ``difficulty_score`` — derived from observed CPT vs. tap-through rate; high
  CPT + low TTR ≈ contested supply. 0 (cheap, well-converting) … 100 (expensive).

These are *directional* (Apple sees a slice of the broader market), but they
cost nothing and beat showing nothing. For arbitrary-keyword lookups we still
need a paid provider eventually — see ``docs/`` Stage 1.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import ASAAdGroup, ASACampaign, ASAMetricDaily, ASASearchTerm
from app.models.credential import ASCCredential
from app.services.asa.analytics import owned_credential_ids
from app.services.keyword_intel.base import KeywordIntel, KeywordIntelProvider


SOURCE_NAME = "asa_search_terms"
DEFAULT_DAYS = 30
# Minimum daily-impression sum before we'll record a row. Without it the
# table fills with thousands of single-impression-noise terms.
_MIN_TOTAL_IMPRESSIONS = 5


def _log_scale_to_100(value: float, ceiling: float) -> int:
    """Log-scale ``value`` against ``ceiling`` and clamp to 0..100.

    Linear scaling collapses heads — most terms see <50 impressions/day while
    a handful see thousands. log10 keeps the long tail visible.
    """
    if value <= 0 or ceiling <= 0:
        return 0
    score = math.log10(1 + value) / math.log10(1 + ceiling) * 100
    return max(0, min(100, round(score)))


def _as_float(value: Decimal | float | None, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def _difficulty_from_cpt_ttr(
    avg_cpt: Decimal | float | None, avg_ttr: Decimal | float | None,
) -> int | None:
    """Cheap heuristic: high CPT and low TTR → harder to win.

    Both inputs are aggregated across the lookback window. Returns None when
    we have neither signal — a row with only impressions can't be scored.
    """
    if avg_cpt is None and avg_ttr is None:
        return None
    # Normalize CPT against $5 (typical ASA top-end is $2–$8) and invert TTR
    # against 5% (above which a term is converting well). Equal-weight blend.
    cpt_pressure = min(1.0, _as_float(avg_cpt) / 5.0)
    ttr_inverse = max(0.0, 1.0 - min(1.0, _as_float(avg_ttr) / 0.05))
    score = (cpt_pressure * 0.6 + ttr_inverse * 0.4) * 100
    return max(0, min(100, round(score)))


class ASASearchTermsProvider(KeywordIntelProvider):
    """Computes Path B intel rows from existing DB tables — no external calls."""

    name = SOURCE_NAME

    async def fetch(
        self, *, app_id: int, session: AsyncSession, days: int = DEFAULT_DAYS,
        **_: Any,
    ) -> list[KeywordIntel]:
        # Resolve the app's adam_id (ASC app id == ASA "app_adam_id").
        app = (
            await session.execute(select(App).where(App.id == app_id))
        ).scalar_one_or_none()
        if app is None or not app.asc_app_id:
            return []
        adam_id = app.asc_app_id

        # Scope metrics to the app owner's own ASA credentials. ``app_adam_id``
        # is a shared Apple id, so two tenants advertising the same store app
        # collide on it; without this filter one tenant's SEARCH_TERM metrics
        # (their real user search queries + spend signals) would leak to the
        # other via keyword-intel refresh (C1). Mirrors asa_recommendations.py.
        owner_id = (
            await session.execute(
                select(ASCCredential.user_id).where(
                    ASCCredential.id == app.credential_id
                )
            )
        ).scalar_one_or_none()
        if owner_id is None:
            return []

        cutoff: date = (datetime.now(timezone.utc) - timedelta(days=days)).date()

        # Pull every metric row for SEARCH_TERM grain since the cutoff for
        # this app, plus the term's text + storefront. The ad_group join lets
        # us fall back to the campaign's storefronts when the metric row's
        # ``storefront`` is null (varies by report flavor).
        stmt = (
            select(
                ASASearchTerm.text,
                ASAMetricDaily.storefront,
                ASACampaign.storefronts,
                ASAMetricDaily.impressions,
                ASAMetricDaily.taps,
                ASAMetricDaily.avg_cpt_amount,
                ASAMetricDaily.ttr,
            )
            .join(ASASearchTerm, ASASearchTerm.id == ASAMetricDaily.dim_id)
            .join(ASAAdGroup, ASAAdGroup.id == ASASearchTerm.ad_group_id)
            .join(ASACampaign, ASACampaign.id == ASAAdGroup.campaign_id)
            .where(
                ASAMetricDaily.dim_kind == "SEARCH_TERM",
                ASAMetricDaily.app_adam_id == adam_id,
                ASAMetricDaily.date >= cutoff,
                ASAMetricDaily.credential_id.in_(owned_credential_ids(owner_id)),
            )
        )
        rows = (await session.execute(stmt)).all()
        if not rows:
            return []

        # Aggregate by (term, storefront). When the metric row's storefront is
        # null, fall back to the first storefront on the parent campaign.
        Aggregate = dict[str, Any]
        by_key: dict[tuple[str, str], Aggregate] = defaultdict(
            lambda: {
                "impressions": 0,
                "taps": 0,
                "cpt_sum": Decimal("0"),
                "cpt_n": 0,
                "ttr_sum": Decimal("0"),
                "ttr_n": 0,
            }
        )
        for text, storefront_row, storefronts, impressions, taps, cpt, ttr in rows:
            sf = storefront_row
            if sf is None and isinstance(storefronts, list) and storefronts:
                sf = str(storefronts[0])
            if sf is None:
                continue
            agg = by_key[(text, sf)]
            agg["impressions"] += int(impressions or 0)
            agg["taps"] += int(taps or 0)
            if cpt is not None:
                agg["cpt_sum"] += Decimal(cpt)
                agg["cpt_n"] += 1
            if ttr is not None:
                agg["ttr_sum"] += Decimal(ttr)
                agg["ttr_n"] += 1

        if not by_key:
            return []

        # Volume normalization is per-fetch: ceiling is the most-observed
        # term in the window. That keeps the score readable as the campaign
        # mix evolves.
        ceiling = max(a["impressions"] for a in by_key.values())

        out: list[KeywordIntel] = []
        for (text, storefront), agg in by_key.items():
            if agg["impressions"] < _MIN_TOTAL_IMPRESSIONS:
                continue
            avg_daily = agg["impressions"] / max(1, days)
            avg_daily_ceiling = ceiling / max(1, days)
            volume = _log_scale_to_100(avg_daily, avg_daily_ceiling)
            avg_cpt = (
                agg["cpt_sum"] / agg["cpt_n"] if agg["cpt_n"] else None
            )
            avg_ttr = (
                agg["ttr_sum"] / agg["ttr_n"] if agg["ttr_n"] else None
            )
            difficulty = _difficulty_from_cpt_ttr(avg_cpt, avg_ttr)
            out.append(
                KeywordIntel(
                    keyword=text,
                    locale=storefront,
                    source=SOURCE_NAME,
                    volume_score=volume,
                    difficulty_score=difficulty,
                    raw_score=int(avg_daily),
                    extra={
                        "lookback_days": days,
                        "total_impressions": agg["impressions"],
                        "total_taps": agg["taps"],
                        "avg_cpt": float(avg_cpt) if avg_cpt is not None else None,
                        "avg_ttr": float(avg_ttr) if avg_ttr is not None else None,
                    },
                )
            )
        return out


__all__ = ["ASASearchTermsProvider", "SOURCE_NAME", "DEFAULT_DAYS"]
