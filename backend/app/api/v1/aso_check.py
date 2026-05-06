"""ASO Check — listing audit endpoint."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.keyword import KeywordTracking
from app.models.metadata import AppMetadataLocalization
from app.schemas.aso_check import AsoCheckOut, IssueOut, IssueSummary
from app.services.aso_check.audit import audit
from app.services.metadata.coloring import classify_keyword

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{app_id}/aso-check", response_model=AsoCheckOut)
async def run_aso_check(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AsoCheckOut:
    """Audit a synced metadata snapshot + tracked keywords for issues.

    Pure read — no writes. Re-run anytime; the rules live in
    ``services/aso_check/audit.py`` so they're easy to extend.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    rows_result = await session.execute(
        select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app_id,
        )
    )
    rows = rows_result.scalars().all()
    app_info = [r for r in rows if r.kind == "app_info"]
    versions = [r for r in rows if r.kind == "version"]

    # Tracked keyword coverage (optional input — same logic as the
    # keyword-coverage endpoint, but flat triples for the auditor).
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
    )
