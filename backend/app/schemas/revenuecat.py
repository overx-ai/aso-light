"""Pydantic schemas for RevenueCat integration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Credential
# ---------------------------------------------------------------------------


class RevenueCatCredentialCreate(BaseModel):
    """Body for ``POST /apps/{app_id}/revenuecat/credential``."""

    name: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1, max_length=255)
    rc_app_id: str | None = Field(default=None, max_length=255)
    secret_key: str = Field(min_length=1)


class RevenueCatCredentialUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=255)
    rc_app_id: str | None = Field(default=None, max_length=255)
    secret_key: str | None = None


class RevenueCatCredentialResponse(BaseModel):
    """Public-safe representation of a RevenueCatCredential.

    The secret key is never echoed back — only metadata.
    """

    id: int
    name: str
    project_id: str
    rc_app_id: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# RC entity passthroughs (kept loose — RC's own JSON shape varies by version)
# ---------------------------------------------------------------------------


class RCProduct(BaseModel):
    id: str
    store_identifier: str
    type: str | None = None
    display_name: str | None = None
    app_id: str | None = None
    is_archived: bool | None = None


class RCEntitlement(BaseModel):
    id: str
    lookup_key: str
    display_name: str | None = None
    is_archived: bool | None = None
    products: list[dict] | None = None


class RCEntitlementCreate(BaseModel):
    lookup_key: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)


class RCEntitlementUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


class RCAttachProductsRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1)


class RCOffering(BaseModel):
    id: str
    lookup_key: str
    display_name: str | None = None
    is_current: bool | None = None
    is_archived: bool | None = None
    metadata: dict | None = None


class RCOfferingCreate(BaseModel):
    lookup_key: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    is_current: bool = False
    metadata: dict | None = None


class RCOfferingUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    is_current: bool | None = None
    metadata: dict | None = None


class RCPackage(BaseModel):
    id: str
    lookup_key: str
    display_name: str | None = None
    position: int | None = None
    products: list[dict] | None = None


class RCPackageCreate(BaseModel):
    lookup_key: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    position: int | None = None


class RCConnectionTestResponse(BaseModel):
    success: bool
    message: str
    apps_count: int | None = None
