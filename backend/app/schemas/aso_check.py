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


class AsoCheckOut(BaseModel):
    summary: IssueSummary
    items: list[IssueOut]
