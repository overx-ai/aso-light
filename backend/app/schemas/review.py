"""Pydantic schemas for customer reviews and developer responses."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN

ReplyTone = Literal["neutral", "apologetic", "appreciative"]


class ReviewResponseOut(BaseModel):
    id: str
    body: str
    last_modified_date: str | None = None
    state: str | None = None


class ReviewOut(BaseModel):
    id: str
    rating: int
    title: str | None = None
    body: str | None = None
    territory: str | None = None
    reviewer_nickname: str | None = None
    created_date: str | None = None
    response: ReviewResponseOut | None = None


class ReviewListOut(BaseModel):
    items: list[ReviewOut]
    next_cursor: str | None = None


class ReviewTrendPoint(BaseModel):
    date: str
    total: int = 0
    low_rating: int = 0
    average_rating: float | None = None
    rating_1: int = 0
    rating_2: int = 0
    rating_3: int = 0
    rating_4: int = 0
    rating_5: int = 0


class ReviewThemeTrendPoint(BaseModel):
    date: str
    count: int = 0
    low_rating: int = 0


class ReviewThemeTrend(BaseModel):
    theme: str
    total: int
    low_rating: int
    points: list[ReviewThemeTrendPoint]


class ReviewTrendInsight(BaseModel):
    kind: Literal["spike", "drop"]
    metric: str
    date: str
    previous_value: int
    value: int
    change: int


class ReviewTrendOut(BaseModel):
    days: int
    start_date: str
    end_date: str
    total_reviews: int
    low_rating_total: int
    average_rating: float | None = None
    low_rating_threshold: int = 2
    truncated: bool = False
    points: list[ReviewTrendPoint]
    themes: list[ReviewThemeTrend]
    insights: list[ReviewTrendInsight]


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=RESPONSE_BODY_MAX_LEN)


class DraftIn(BaseModel):
    tone: ReplyTone = "neutral"


class DraftOut(BaseModel):
    suggestion: str
    locale: str


class TranslateReviewIn(BaseModel):
    target_locale: str = Field(min_length=2)


class TranslateReviewOut(BaseModel):
    translation: str
    cached: bool
