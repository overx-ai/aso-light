"""MCP tools for ASO Check — listing audit across locales."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.app import App
from app.models.iap import InAppPurchase
from app.models.keyword import KeywordTracking
from app.models.metadata import AppMetadataLocalization
from app.models.subscription import Subscription, SubscriptionGroup
from app.models.territory import Territory
from app.schemas.aso_check import (
    AsoCheckOut,
    IssueOut,
    IssueSummary,
    PaidCoverage,
    RecommendationOut,
)
from app.services.aso_check.audit import audit
from app.services.aso_check.pricing import (
    build_pricing_recommendations,
    build_pricing_snapshots,
)
from app.services.metadata.coloring import classify_keyword


@mcp.tool(name="aso.aso_check")
async def aso_check_tool(app_id: int) -> AsoCheckOut:
    """Run the listing audit on a synced metadata snapshot + tracked keywords.

    Pure read — no writes. Surfaces metadata gaps plus pricing opportunities
    from cached storefront data. Re-run anytime; the rules live in
    ``app.services.aso_check``.
    """
    async with session_scope() as session:
        await resolve_app(app_id, session)

        rows_result = await session.execute(
            select(AppMetadataLocalization).where(
                AppMetadataLocalization.app_id == app_id,
            )
        )
        rows = rows_result.scalars().all()
        app_info = [r for r in rows if r.kind == "app_info"]
        versions = [r for r in rows if r.kind == "version"]

        trackings_result = await session.execute(
            select(KeywordTracking)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == app_id)
        )
        trackings = trackings_result.scalars().all()

        coverage_triples: list[tuple[str, str, str]] = []
        if trackings and rows:
            by_locale: dict[str, dict[str, str | None]] = {}
            for r in rows:
                bucket = by_locale.setdefault(
                    r.locale,
                    {"name": None, "subtitle": None, "keywords": None},
                )
                if r.kind == "app_info":
                    bucket["name"] = r.name
                    bucket["subtitle"] = r.subtitle
                else:
                    bucket["keywords"] = r.keywords
            for tr in trackings:
                for locale, fields in by_locale.items():
                    placement = classify_keyword(
                        tr.keyword.text,
                        fields["name"],
                        fields["subtitle"],
                        fields["keywords"],
                    )
                    coverage_triples.append((tr.keyword.text, locale, placement))

        issues = audit(
            app_info=app_info,
            versions=versions,
            tracked_coverage=coverage_triples or None,
        )

        summary = IssueSummary(
            errors=sum(1 for i in issues if i.severity == "error"),
            warnings=sum(1 for i in issues if i.severity == "warning"),
            infos=sum(1 for i in issues if i.severity == "info"),
            locales_audited=len({r.locale for r in rows}),
        )

        from app.services.asa.joins import paid_organic_join

        paid = await paid_organic_join(session=session, app_id=app_id, days=30)
        paid_coverage = (
            PaidCoverage(
                tracked_with_paid=[
                    p["term"] for p in paid if p["paid_impressions_30d"] > 0
                ],
                tracked_without_paid=[
                    p["term"] for p in paid if p["paid_impressions_30d"] == 0
                ],
            )
            if paid
            else None
        )

        app_result = await session.execute(
            select(App)
            .options(
                selectinload(App.subscription_groups)
                .selectinload(SubscriptionGroup.subscriptions)
                .selectinload(Subscription.prices),
                selectinload(App.iaps).selectinload(InAppPurchase.prices),
            )
            .where(App.id == app_id)
        )
        app_record = app_result.scalar_one()

        territories_result = await session.execute(select(Territory))
        territories = territories_result.scalars().all()
        territory_by_id = {territory.id: territory for territory in territories}

        pricing_recommendations = build_pricing_recommendations(
            build_pricing_snapshots(
                app_id=app_id,
                subscription_groups=app_record.subscription_groups,
                iaps=app_record.iaps,
                territory_by_id=territory_by_id,
            )
        )

        return AsoCheckOut(
            summary=summary,
            items=[
                IssueOut(
                    severity=i.severity,
                    locale=i.locale,
                    field=i.field,
                    code=i.code,
                    message=i.message,
                    suggestion=i.suggestion,
                )
                for i in issues
            ],
            paid_coverage=paid_coverage,
            recommendations=[
                RecommendationOut(
                    id=item.id,
                    category=item.category,
                    priority=item.priority,
                    title=item.title,
                    body=item.body,
                    facts=item.facts,
                    cta_label=item.cta_label,
                    cta_path=item.cta_path,
                )
                for item in pricing_recommendations
            ],
        )
