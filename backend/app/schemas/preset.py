"""Schemas for price preset CRUD endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PresetCreate(BaseModel):
    """Request body for creating a price preset."""

    name: str
    base_territory_code: str = "US"
    base_price: float
    index_type: str
    apply_vat: bool = False
    charming_mode: str = "none"
    config: dict[str, Any] | None = None


class PresetUpdate(BaseModel):
    """Request body for updating a price preset (all fields optional)."""

    name: str | None = None
    base_territory_code: str | None = None
    base_price: float | None = None
    index_type: str | None = None
    apply_vat: bool | None = None
    charming_mode: str | None = None
    config: dict[str, Any] | None = None


class PresetResponse(BaseModel):
    """Response schema for a price preset."""

    id: int
    name: str
    base_territory_code: str
    base_price: float
    index_type: str
    apply_vat: bool
    charming_mode: str
    config: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
