"""Schemas for app growth recommendations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RecommendationCategory = Literal[
    "pricing",
    "metadata",
    "keywords",
    "visibility",
    "reviews",
    "paid_search",
    "availability",
]
RecommendationSeverity = Literal["critical", "warning", "info"]


class RecommendationEvidence(BaseModel):
    label: str
    value: str


class GrowthRecommendationOut(BaseModel):
    id: str
    category: RecommendationCategory
    severity: RecommendationSeverity
    title: str
    description: str
    impact: str
    cta_label: str
    cta_path: str
    evidence: list[RecommendationEvidence] = Field(default_factory=list)


class GrowthRecommendationSummary(BaseModel):
    total: int
    pricing: int


class GrowthRecommendationsOut(BaseModel):
    summary: GrowthRecommendationSummary
    items: list[GrowthRecommendationOut]
