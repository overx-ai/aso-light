"""Schemas for territory API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TerritoryResponse(BaseModel):
    """A single App Store territory."""

    id: int
    code: str
    name: str
    currency_code: str
    vat_rate: float
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
