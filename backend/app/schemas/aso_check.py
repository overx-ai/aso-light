"""Schemas for ASO Check (listing audit + growth recommendations)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warning", "info"]
RecommendationCategory = Literal["pricing"]
RecommendationPriority = Literal["high", "medium", "low"]


class IssueOut(BaseModel):
    severity: Severity
    locale: str | None
    field: str | None
    code: str
    message: str
    suggestion: str | None = None


class IssueSummary(BaseModel):
    errors: int
    warnings: int
    infos: int
    locales_audited: int


class PaidCoverage(BaseModel):
    """ASA paid-keyword coverage of the app's tracked organic terms.

    Populated by ``aso.aso_check`` when ASA data is present for the app.
    ``tracked_with_paid`` lists tracked terms that have non-zero impressions
    in ASA over the last 30 days; ``tracked_without_paid`` lists those that
    do not, surfacing organic-only coverage gaps.
    """

    tracked_with_paid: list[str]
    tracked_without_paid: list[str]


class RecommendationOut(BaseModel):
    id: str
    category: RecommendationCategory
    priority: RecommendationPriority
    title: str
    body: str
    facts: list[str] = Field(default_factory=list)
    cta_label: str | None = None
    cta_path: str | None = None


class AsoCheckOut(BaseModel):
    summary: IssueSummary
    items: list[IssueOut]
    paid_coverage: PaidCoverage | None = None
    recommendations: list[RecommendationOut] = Field(default_factory=list)
