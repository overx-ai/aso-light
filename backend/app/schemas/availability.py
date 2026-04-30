"""Schemas for the App Availability API."""

from __future__ import annotations

from pydantic import BaseModel


class TerritoryAvailability(BaseModel):
    territory_code: str  # alpha-2
    territory_name: str
    available: bool
    preorder_enabled: bool = False


class AppAvailabilityResponse(BaseModel):
    available_in_new_territories: bool
    territories: list[TerritoryAvailability]


class AppAvailabilityUpdateRequest(BaseModel):
    available_in_new_territories: bool = True
    disabled_territories: list[str]  # alpha-2 codes the user wants OFF
