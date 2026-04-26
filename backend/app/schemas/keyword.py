"""Pydantic schemas for keyword-related API endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ---- Keyword Core ----


class KeywordCreate(BaseModel):
    text: str
    locale: str = "en-US"


class KeywordResponse(BaseModel):
    id: int
    text: str
    locale: str
    popularity: int | None = None
    popularity_updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ---- Keyword Tracking ----


class KeywordTrackingResponse(BaseModel):
    id: int
    keyword: KeywordResponse
    app_id: int
    latest_rank: int | None = None
    rank_change: int | None = None
    added_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---- Ranking History ----


class RankDataPoint(BaseModel):
    date: datetime
    rank: int | None = None
    territory_code: str


class KeywordRankingHistory(BaseModel):
    keyword_text: str
    territory_code: str
    data_points: list[RankDataPoint]


# ---- Suggestions / Search ----


class KeywordSearchRequest(BaseModel):
    term: str
    locale: str = "en_us"


class KeywordSuggestion(BaseModel):
    term: str


class SearchResult(BaseModel):
    position: int
    app_id: str
    name: str
    bundle_id: str
    icon_url: str


# ---- Cross-Localization ----


class CrossLocalizationEntry(BaseModel):
    territory_code: str
    locale: str
    is_indexed: bool


# ---- Competitors ----


class CompetitorCreate(BaseModel):
    asc_app_id: str
    name: str
    bundle_id: str | None = None


class CompetitorResponse(BaseModel):
    id: int
    asc_app_id: str
    name: str
    bundle_id: str | None = None
    app_id: int

    model_config = ConfigDict(from_attributes=True)


class CompetitorKeywordResult(BaseModel):
    keyword_text: str
    competitor_rank: int | None = None
    our_rank: int | None = None
    territory_code: str
