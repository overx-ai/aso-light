"""Pydantic schemas for the keyword-intelligence subsystem."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KeywordIntelOut(BaseModel):
    """One stored intel row, as exposed via REST/MCP."""

    keyword: str
    locale: str
    source: str
    volume_score: int | None
    difficulty_score: int | None
    raw_score: int | None
    extra: dict[str, Any] | None
    fetched_at: datetime


class KeywordIntelRefreshOut(BaseModel):
    """Result of a refresh run."""

    written_total: int
    by_source: dict[str, int] = Field(default_factory=dict)
    skipped_sources: dict[str, str] = Field(default_factory=dict)
