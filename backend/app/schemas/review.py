"""Pydantic schemas for customer reviews and developer responses."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN
from app.services.reviews.draft import ReplyTone, ReviewTheme


class ReviewResponseOut(BaseModel):
    id: str
    body: str
    last_modified_date: str | None = None
    state: str | None = None


class ReviewOut(BaseModel):
    id: str
    rating: int
    theme: ReviewTheme
    reply_template: str
    title: str | None = None
    body: str | None = None
    territory: str | None = None
    reviewer_nickname: str | None = None
    created_date: str | None = None
    response: ReviewResponseOut | None = None


class ReviewListOut(BaseModel):
    items: list[ReviewOut]
    next_cursor: str | None = None


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=RESPONSE_BODY_MAX_LEN)


class DraftIn(BaseModel):
    tone: ReplyTone | None = None


class DraftOut(BaseModel):
    suggestion: str
    locale: str
    theme: ReviewTheme
    tone: ReplyTone
    reply_template: str


class TranslateReviewIn(BaseModel):
    target_locale: str = Field(min_length=2)


class TranslateReviewOut(BaseModel):
    translation: str
    cached: bool
