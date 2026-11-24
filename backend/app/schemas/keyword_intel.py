"""Pydantic schemas for the keyword-intelligence subsystem."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.data.storefronts import STOREFRONT_ALIASES, STOREFRONTS


class KeywordIntelOut(BaseModel):
    """One stored intel row, as exposed via REST/MCP."""

    keyword: str
    locale: str
    source: str
    volume_score: int | None
    difficulty_score: int | None
    raw_score: int | None
    popularity_source: str | None = None
    extra: dict[str, Any] | None
    fetched_at: datetime


class KeywordIntelRefreshOut(BaseModel):
    """Result of a refresh run."""

    written_total: int
    by_source: dict[str, int] = Field(default_factory=dict)
    skipped_sources: dict[str, str] = Field(default_factory=dict)


class FreeTermScore(BaseModel):
    term: str
    storefront: str
    popularity: int
    popularity_source: str
    hints_popularity: int
    asa_popularity: int | None = None
    difficulty: int


class MinedCandidate(BaseModel):
    term: str
    count: int


class FreeScoreOut(BaseModel):
    storefront: str
    scores: list[FreeTermScore]
    candidates: list[MinedCandidate] = Field(default_factory=list)


class NicheOut(BaseModel):
    term: str
    popularity: int
    difficulty: int
    real_apps: int
    weak_apps: int
    median_ratings: int
    opportunity: int
    top_titles: list[str] = Field(default_factory=list)


class NicheScanOut(BaseModel):
    storefront: str
    seeds: list[str]
    scanned: int
    niches: list[NicheOut]


MAX_NICHE_SEEDS = 3


class NicheScanIn(BaseModel):
    seeds: list[str] = Field(min_length=1, max_length=MAX_NICHE_SEEDS)
    storefront: str = "US"

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, v: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(s.strip() for s in v if s.strip()))
        if not cleaned:
            raise ValueError("seeds must hold at least one non-empty term")
        return cleaned

    @field_validator("storefront")
    @classmethod
    def _storefront(cls, v: str) -> str:
        code = v.strip().lower()
        code = STOREFRONT_ALIASES.get(code, code)
        if code not in STOREFRONTS:
            raise ValueError("expected an alpha-2 storefront code like US")
        return code.upper()


class VariantPickOut(BaseModel):
    source_term: str
    locale: str
    best: str
    score: int
    alternatives: list[str] = Field(default_factory=list)
    not_searched_here: bool


class PackFieldsOut(BaseModel):
    storefront: str
    picks: list[VariantPickOut]
    expansion: dict[str, list[str]] = Field(default_factory=dict)
    values_by_locale: dict[str, str]
    bulk_preview: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
