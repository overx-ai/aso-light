from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

GrowthCategory = Literal[
    "setup",
    "metadata",
    "keywords",
    "paid_search",
    "reviews",
    "pricing",
]
GrowthPriority = Literal["high", "medium", "low"]


class GrowthRecommendationOut(BaseModel):
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


class GrowthRecommendationsOut(BaseModel):
    items: list[GrowthRecommendationOut]
