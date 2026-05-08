from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AccountUserSummary(BaseModel):
    id: int
    email: str
    name: str


class AccountPersonalAccessTokenSummary(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


class AccountCredentialSummary(BaseModel):
    id: int
    name: str
    issuer_id: str
    key_id: str
    apps_count: int


class AccountAppSummary(BaseModel):
    id: int
    name: str
    bundle_id: str
    asc_app_id: str
    credential_id: int


class AccountWhoAmIResponse(BaseModel):
    auth_type: Literal["personal_access_token"] = "personal_access_token"
    user: AccountUserSummary
    personal_access_token: AccountPersonalAccessTokenSummary
    credential_count: int
    app_count: int
    asc_credentials: list[AccountCredentialSummary]
    apps: list[AccountAppSummary]
