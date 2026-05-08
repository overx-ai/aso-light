"""Schemas for ASO Check (listing audit)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["error", "warning", "info"]


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


class AsoCheckOut(BaseModel):
    summary: IssueSummary
    items: list[IssueOut]
    paid_coverage: PaidCoverage | None = None
