"""MCP tools for ASO Check — listing audit across locales."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.keyword import KeywordTracking
from app.models.metadata import AppMetadataLocalization
from app.schemas.aso_check import AsoCheckOut, IssueOut, IssueSummary, PaidCoverage
from app.services.aso_check.audit import audit
from app.services.metadata.coloring import classify_keyword


@mcp.tool(name="aso.aso_check")
async def aso_check_tool(app_id: int) -> AsoCheckOut:
    """Run the listing audit on a synced metadata snapshot + tracked keywords.

    Pure read — no writes. Surfaces empty fields, character-limit overflows,
    keyword quality, and tracked-keyword coverage gaps. Re-run anytime; the
    rules live in ``app.services.aso_check.audit``.
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
        )
