"""Schemas for App Clash (cross-app metadata comparison)."""
from __future__ import annotations

from pydantic import BaseModel


class ClashRow(BaseModel):
    track_id: str
    is_self: bool
    name: str | None = None
    subtitle: str | None = None
    seller: str | None = None
    primary_genre: str | None = None
    average_rating: float | None = None
    rating_count: int | None = None
    release_date: str | None = None
    version: str | None = None
    file_size_mb: float | None = None
    price: float | None = None
    currency: str | None = None
    formatted_price: str | None = None
    icon_url: str | None = None
    bundle_id: str | None = None
    description_excerpt: str | None = None


class AppClashOut(BaseModel):
    country: str
    rows: list[ClashRow]
