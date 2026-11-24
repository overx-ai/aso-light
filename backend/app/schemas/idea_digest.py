from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.keyword_intel import NicheOut

IdeaStatus = Literal["new", "approved", "rejected"]


class IdeaOut(NicheOut):
    status: str
    created: date
    updated: date
    topic: str
    category: str
    storefront: str


class DigestState(BaseModel):
    last_run: date | None = None
    unsent: list[str] = Field(default_factory=list)
    topics: dict[str, date] = Field(default_factory=dict)
