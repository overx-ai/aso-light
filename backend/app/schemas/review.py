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


class ReviewTrendPointOut(BaseModel):
    date: str
    total_reviews: int
    low_rating_reviews: int
    replied_reviews: int
    average_rating: float | None = None


class ReviewTrendSummaryOut(BaseModel):
    total_reviews: int
    low_rating_reviews: int
    replied_reviews: int
    average_rating: float | None = None
    low_rating_share_pct: float
    response_rate_pct: float
    latest_total_reviews: int
    latest_low_rating_reviews: int
    biggest_spike_date: str | None = None
    biggest_spike_delta: int = 0
    biggest_drop_date: str | None = None
    biggest_drop_delta: int = 0


class ReviewTrendOut(BaseModel):
    days: int
    low_rating_max: int
    territory: str | None = None
    partial: bool = False
    points: list[ReviewTrendPointOut]
    summary: ReviewTrendSummaryOut


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
