from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword import Keyword, KeywordTracking
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.review_theme import ReviewThemeCache
from app.services.asa.joins import (
    suggest_negative_candidates,
    suggest_organic_keywords_to_track,
)
from app.services.metadata.coloring import classify_keyword

GrowthPriority = Literal["high", "medium", "low"]
GrowthCategory = Literal[
    "setup",
    "metadata",
    "keywords",
    "paid_search",
    "reviews",
    "pricing",
]


@dataclass(frozen=True)
class GrowthRecommendation:
    id: str
    category: GrowthCategory
    priority: GrowthPriority
    confidence: GrowthPriority
    effort: GrowthPriority
    title: str
    detail: str
    evidence: dict[str, Any]
    cta_label: str
    cta_path: str


async def generate_growth_recommendations(
    *,
    session: AsyncSession,
    app_id: int,
) -> list[GrowthRecommendation]:
    recommendations: list[GrowthRecommendation] = []

    metadata_synced = await _metadata_is_synced(session, app_id)
    tracked_keywords = await _tracked_keywords(session, app_id)

    if not metadata_synced:
        recommendations.append(
            GrowthRecommendation(
                id="metadata.sync",
                category="setup",
                priority="high",
                confidence="high",
                effort="low",
                title="Sync App Store metadata",
                detail=(
                    "Growth recommendations need the current title, subtitle, "
                    "keyword, and promotional text snapshot before suggesting copy changes."
                ),
                evidence={"metadata_synced": False},
                cta_label="Sync metadata",
                cta_path=f"/apps/{app_id}/metadata",
            )
        )

    if len(tracked_keywords) < 5:
        recommendations.append(
            GrowthRecommendation(
                id="keywords.expand_tracking",
                category="keywords",
                priority="medium",
                confidence="high",
                effort="low",
                title="Track more organic keywords",
                detail=(
                    "You are tracking fewer than five keywords, which leaves "
                    "too little rank evidence for metadata decisions."
                ),
                evidence={
                    "tracked_keywords": len(tracked_keywords),
                    "recommended_minimum": 5,
                },
                cta_label="Open keywords",
                cta_path=f"/apps/{app_id}/keywords",
            )
        )

    if metadata_synced and tracked_keywords:
        missing = await _keywords_missing_from_metadata(
            session=session,
            app_id=app_id,
            keywords=tracked_keywords,
        )
        if missing:
            recommendations.append(
                GrowthRecommendation(
                    id="metadata.keyword_coverage",
                    category="metadata",
                    priority="high",
                    confidence="medium",
                    effort="medium",
                    title="Use tracked keywords in editable metadata",
                    detail=(
                        "Some tracked keywords are not present in any cached "
                        "title, subtitle, or keyword field. Add the best terms "
                        "where they fit naturally."
                    ),
                    evidence={
                        "missing_keywords": missing[:5],
                        "missing_count": len(missing),
                    },
                    cta_label="Open metadata",
                    cta_path=f"/apps/{app_id}/metadata",
                )
            )

    paid_winners = await suggest_organic_keywords_to_track(
        session=session,
        app_id=app_id,
        days=30,
        min_taps=20,
    )
    if paid_winners:
        top = max(paid_winners, key=lambda row: (row["installs"], row["taps"]))
        recommendations.append(
            GrowthRecommendation(
                id="asa.track_paid_winners",
                category="paid_search",
                priority="high",
                confidence="high",
                effort="low",
                title="Track paid search terms organically",
                detail=(
                    "Search Ads has terms with enough taps that are not in "
                    "your organic tracker. Add the winners so rank movement "
                    "can guide metadata work."
                ),
                evidence={
                    "candidate_count": len(paid_winners),
                    "top_term": top["text"],
                    "top_taps": top["taps"],
                    "top_installs": top["installs"],
                },
                cta_label="Open paid search",
                cta_path=f"/apps/{app_id}/paid-search",
            )
        )

    negative_candidates = await suggest_negative_candidates(
        session=session,
        app_id=app_id,
        days=30,
        min_spend=10.0,
        max_conv_rate=0.005,
    )
    if negative_candidates:
        top_waste = max(negative_candidates, key=lambda row: row["spend"])
        recommendations.append(
            GrowthRecommendation(
                id="asa.add_negative_keywords",
                category="paid_search",
                priority="high",
                confidence="high",
                effort="low",
                title="Cut wasteful Search Ads terms",
                detail=(
                    "Some search terms crossed the spend threshold while "
                    "converting poorly. Review them as negative-keyword candidates."
                ),
                evidence={
                    "candidate_count": len(negative_candidates),
                    "top_term": top_waste["text"],
                    "top_spend": top_waste["spend"],
                    "top_conversion_rate": top_waste["conversion_rate"],
                },
                cta_label="Review negatives",
                cta_path=f"/apps/{app_id}/paid-search",
            )
        )

    severe_reviews = await _severe_review_count(session, app_id)
    if severe_reviews:
        recommendations.append(
            GrowthRecommendation(
                id="reviews.triage_severe",
                category="reviews",
                priority="high",
                confidence="medium",
                effort="medium",
                title="Triage high-severity reviews",
                detail=(
                    "Recent review classifications include severe bugs, UX, "
                    "pricing, or support issues. Handle these before pushing "
                    "more acquisition traffic."
                ),
                evidence={"severe_review_count": severe_reviews},
                cta_label="Open reviews",
                cta_path=f"/apps/{app_id}/reviews",
            )
        )

    return sorted(
        recommendations,
        key=lambda rec: (
            {"high": 0, "medium": 1, "low": 2}[rec.priority],
            {"low": 0, "medium": 1, "high": 2}[rec.effort],
            rec.id,
        ),
    )


async def _metadata_is_synced(session: AsyncSession, app_id: int) -> bool:
    state = (
        await session.execute(
            select(AppMetadataState.id).where(AppMetadataState.app_id == app_id)
        )
    ).scalar_one_or_none()
    if state is None:
        return False
    count = (
        await session.execute(
            select(func.count(AppMetadataLocalization.id)).where(
                AppMetadataLocalization.app_id == app_id
            )
        )
    ).scalar_one()
    return int(count or 0) > 0


async def _tracked_keywords(session: AsyncSession, app_id: int) -> list[str]:
    rows = (
        await session.execute(
            select(Keyword.text)
            .join(KeywordTracking, KeywordTracking.keyword_id == Keyword.id)
            .where(KeywordTracking.app_id == app_id)
            .order_by(Keyword.text)
        )
    ).all()
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        text = row[0].strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


async def _keywords_missing_from_metadata(
    *,
    session: AsyncSession,
    app_id: int,
    keywords: list[str],
) -> list[str]:
    rows = (
        await session.execute(
            select(AppMetadataLocalization).where(
                AppMetadataLocalization.app_id == app_id
            )
        )
    ).scalars().all()
    metadata_by_locale: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = metadata_by_locale.setdefault(row.locale, {})
        if row.kind == "app_info":
            bucket["name"] = row.name
            bucket["subtitle"] = row.subtitle
        elif row.kind == "version":
            bucket["keywords"] = row.keywords

    missing: list[str] = []
    for keyword in keywords:
        covered = any(
            classify_keyword(
                keyword,
                fields.get("name"),
                fields.get("subtitle"),
                fields.get("keywords"),
            )
            != "none"
            for fields in metadata_by_locale.values()
        )
        if not covered:
            missing.append(keyword)
    return missing


_SEVERE_REVIEW_WINDOW_DAYS = 30


async def _severe_review_count(session: AsyncSession, app_id: int) -> int:
    # Limit to recent classifications so the recommendation fades once the
    # team has worked through the backlog and classified new reviews arrive.
    since = datetime.now(tz=UTC) - timedelta(days=_SEVERE_REVIEW_WINDOW_DAYS)
    count = (
        await session.execute(
            select(func.count(ReviewThemeCache.id)).where(
                ReviewThemeCache.app_id == app_id,
                ReviewThemeCache.severity >= 4,
                ReviewThemeCache.theme.in_(["bug", "ux", "pricing", "support"]),
                ReviewThemeCache.classified_at >= since,
            )
        )
    ).scalar_one()
    return int(count or 0)
