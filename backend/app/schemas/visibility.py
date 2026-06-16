"""Pydantic schemas for the keyword visibility tracker."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.data.territories import ALPHA2_TO_ALPHA3


def is_known_storefront(code: str) -> bool:
    """True if ``code`` is a known App Store territory/storefront (alpha-2,
    case-insensitive). Storefronts are lowercase alpha-2 (e.g. "us")."""
    return code.strip().upper() in ALPHA2_TO_ALPHA3


class WatchCreate(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    # Lowercase ISO-2 storefront (e.g. "us"). The visibility poller feeds this
    # straight to iTunes as a 2-letter storefront, so anything longer would
    # silently create a broken watch. Unknown-but-2-char codes are rejected at
    # the endpoint (400 / ToolError) so callers get a clear, layer-native error.
    country: str = Field(min_length=2, max_length=2)


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
