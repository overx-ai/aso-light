"""Pydantic schemas for the clone-and-version-bump flow."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CloneScope(BaseModel):
    """Toggles for what to copy from the source product."""

    localizations: bool = True
    price_schedule: bool = True
    intro_offers: bool = True
    screenshot: bool = True
    auto_archive: bool = True
    group_availability: bool = True


class ClonePreviewResponse(BaseModel):
    """Result of ``GET /clone/preview`` — what the run *would* do.

    Lets the UI show a confirmation modal with concrete counts before
    the user fires the actual clone.
    """

    suggested_product_id: str
    source_product_id: str
    locale_count: int
    priced_territory_count: int
    intro_offer_count: int
    has_screenshot: bool
    revenuecat_connected: bool
    revenuecat_old_product_found: bool
    revenuecat_attached_entitlements: int
    revenuecat_attached_packages: int


class CloneRequest(BaseModel):
    """Body of ``POST /apps/{id}/subscriptions/{id}/clone``."""

    new_product_id: str = Field(min_length=1, max_length=255)
    new_name: str | None = Field(default=None, max_length=64)
    scope: CloneScope = Field(default_factory=CloneScope)
    swap_revenuecat: bool = True


class CloneStepStatus(BaseModel):
    name: str
    status: str  # "pending" | "running" | "done" | "skipped" | "failed"
    detail: str | None = None
    completed: int | None = None
    total: int | None = None


class CloneOperationOut(BaseModel):
    id: int
    app_id: int
    source_kind: str
    source_local_id: int
    source_product_id: str
    target_product_id: str
    source_asc_id: str
    target_asc_id: str | None
    scope: CloneScope
    asc_steps: list[CloneStepStatus]
    revenuecat_steps: list[CloneStepStatus]
    status: str
    error_log: list[str]
    created_at: datetime
    completed_at: datetime | None
