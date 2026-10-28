from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.data.storefronts import DEFAULT_COUNTRY
from app.schemas.keyword_intel import VariantPickOut
from app.services.metadata.coloring import KeywordPlacement

DEFAULT_LISTING_STOREFRONT = DEFAULT_COUNTRY.upper()
ExportSource = Literal["current", "proposal"]


class ListingProposal(BaseModel):
    name: str
    subtitle: str
    keywords: str


class ScoredProposal(ListingProposal):
    covered_popularity: int
    delta_vs_current: int


class BrainstormTerm(BaseModel):
    term: str
    popularity: int
    difficulty: int
    value: float
    popularity_source: str | None = None


class BrainstormIn(BaseModel):
    storefront: str = Field(default=DEFAULT_LISTING_STOREFRONT, min_length=2, max_length=3)
    competitor_track_id: str | None = Field(default=None, pattern=r"^\d+$", max_length=20)


class BrainstormOut(BaseModel):
    storefront: str
    source_locale: str
    competitor_track_id: str | None = None
    using: list[BrainstormTerm] = Field(default_factory=list)
    missing: list[BrainstormTerm] = Field(default_factory=list)
    variants: list[VariantPickOut] = Field(default_factory=list)


class GapCell(BaseModel):
    keyword: str
    locale: str
    storefront: str
    rank: int | None = None
    variant: str | None = None
    placement: KeywordPlacement


class GapsOut(BaseModel):
    locales: list[str]
    keywords: list[str]
    cells: list[GapCell]


class RankingsRefreshOut(BaseModel):
    territories: list[str]
    recorded: int


class ProposeIn(BaseModel):
    storefront: str = Field(default=DEFAULT_LISTING_STOREFRONT, min_length=2, max_length=3)


class ProposalsOut(BaseModel):
    storefront: str
    source_locale: str
    current: ScoredProposal
    proposals: list[ScoredProposal]


class LocalizeIn(BaseModel):
    proposal: ListingProposal
    storefront: str = DEFAULT_LISTING_STOREFRONT
    locales: list[str] | None = None


class ListingBulkPreview(BaseModel):
    """Arguments for ``metadata_bulk_preview`` / ``POST .../metadata/bulk/preview``."""

    field: str
    value: str | None = None
    target_locales: list[str]
    values_by_locale: dict[str, str]


class LocalizeOut(BaseModel):
    storefront: str
    source_locale: str
    bulk_previews: list[ListingBulkPreview] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)
    localized_locales: list[str] = Field(default_factory=list)
    remaining_locales: list[str] = Field(default_factory=list)
    picks: list[VariantPickOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExportLocale(BaseModel):
    name: str | None = None
    subtitle: str | None = None
    keywords: str | None = None
    promotional_text: str | None = None
    description: str | None = None


class ExportOut(BaseModel):
    app_id: int
    source: ExportSource
    locales: dict[str, ExportLocale]
