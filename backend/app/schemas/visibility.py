"""Pydantic schemas for the keyword visibility tracker."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WatchCreate(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=2, max_length=8)


class VisibilityResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    track_id: str
    name: str
    bundle_id: str
    icon_url: str


class VisibilitySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    polled_at: datetime
    results_count: int
    results: list[VisibilityResultOut] = []


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    country: str
    last_polled_at: datetime | None = None
    latest_snapshot: VisibilitySnapshotOut | None = None


class WatchListOut(BaseModel):
    items: list[WatchOut]


class SnapshotListOut(BaseModel):
    items: list[VisibilitySnapshotOut]


class SovEntry(BaseModel):
    track_id: str
    name: str
    icon_url: str
    appearances: int  # in top 3
    polls: int        # total polls in window
    sov_pct: float    # appearances / polls * 100


class SovOut(BaseModel):
    watch_id: int
    text: str
    country: str
    polls: int
    days: int
    entries: list[SovEntry]


class FullSovOut(BaseModel):
    items: list[SovOut]


# ---- Anomalies ----


class AnomalyOut(BaseModel):
    kind: str  # "surge" | "drop" | "new" | "gone"
    track_id: str
    name: str
    icon_url: str
    prev_median_position: int | None = None
    latest_position: int | None = None
    delta: int


class WatchAnomaliesOut(BaseModel):
    watch_id: int
    text: str
    country: str
    polls: int
    anomalies: list[AnomalyOut]


class AnomaliesOut(BaseModel):
    items: list[WatchAnomaliesOut]
