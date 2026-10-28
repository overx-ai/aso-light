"""MCP tools for the listing optimizer (spec 019). Nothing here writes to ASC;
``metadata_bulk_apply`` stays the only apply path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastmcp.exceptions import ToolError

from app.core.config import settings
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.schemas.listing import (
    DEFAULT_LISTING_STOREFRONT,
    BrainstormOut,
    ExportOut,
    ExportSource,
    GapsOut,
    ListingProposal,
    LocalizeOut,
    ProposalsOut,
)
from app.services.listing.errors import ListingError
from app.services.listing.service import ListingOptimizerService
from app.services.metadata.translate import build_translator

T = TypeVar("T")


async def _with_service(app_id: int, call: Callable[[ListingOptimizerService], Awaitable[T]]) -> T:
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        service = ListingOptimizerService(session, app, translator=build_translator(settings))
        try:
            return await call(service)
        except ListingError as exc:
            raise ToolError(str(exc)) from exc


@mcp.tool(name="listing_brainstorm")
async def listing_brainstorm(
    app_id: int,
    storefront: str = DEFAULT_LISTING_STOREFRONT,
    competitor_track_id: str | None = None,
    refresh: bool = False,
) -> BrainstormOut:
    """Keywords the listing uses vs. the valuable ones it is missing, for one storefront.

    ``using`` = n-grams of the app's own name/subtitle/keywords/description;
    ``missing`` = hints expansion + competitor title mining, scored by free popularity
    and organic difficulty, value above the threshold, never in ``using``. Missing and
    tracked terms get native variants per live locale (billed AI translations).
    Returns the stored result unless ``refresh`` or nothing is stored yet.
    ``competitor_track_id`` runs the same path on another app's public listing
    (name + description) and stores nothing. Promote terms with ``keywords_add``.
    """
    async def call(service: ListingOptimizerService) -> BrainstormOut:
        if not refresh and not competitor_track_id:
            stored = await service.stored_brainstorm(storefront)
            if stored is not None:
                return stored
        return await service.brainstorm(storefront, competitor_track_id)

    return await _with_service(app_id, call)


@mcp.tool(name="listing_gaps")
async def listing_gaps(app_id: int) -> GapsOut:
    """Tracked keyword x live locale grid: latest rank in the locale's storefront,
    the brainstormed native variant, and placement. ``missing_localized`` = neither
    the term nor its variant is in that locale's listing and the term ranks poorly
    (or is unranked) there. Read-only; ranks come from the stored rankings."""
    return await _with_service(app_id, lambda s: s.gaps())


@mcp.tool(name="listing_propose")
async def listing_propose(app_id: int, storefront: str = DEFAULT_LISTING_STOREFRONT) -> ProposalsOut:
    """Up to 3 name/subtitle/keywords sets for the main storefront from the stored
    brainstorm (run ``listing_brainstorm`` first). Sets breaking a char limit or
    repeating a word across fields are dropped. Each carries the popularity it
    covers and the delta vs. the current listing. One billed AI call (monthly cap).
    Suggestion-only."""
    return await _with_service(app_id, lambda s: s.propose(storefront))


@mcp.tool(name="listing_localize")
async def listing_localize(
    app_id: int,
    name: str,
    subtitle: str,
    keywords: str,
    storefront: str = DEFAULT_LISTING_STOREFRONT,
    locales: list[str] | None = None,
) -> LocalizeOut:
    """Localize one proposal into the live locales (default: all, 4 per call plus the
    source locale; call again with ``remaining_locales``). Name/subtitle are
    translated, the keyword field is re-packed per storefront from the best-searched
    native variants. Only fields in ``editable_fields`` are produced (``locked_fields``
    lists the rest). Pass each ``bulk_previews`` item to ``metadata_bulk_preview``,
    then ``metadata_bulk_apply`` to write. Billed AI translations."""
    proposal = ListingProposal(name=name, subtitle=subtitle, keywords=keywords)
    return await _with_service(app_id, lambda s: s.localize(proposal, storefront, locales))


@mcp.tool(name="metadata_export")
async def metadata_export(app_id: int, source: ExportSource = "current") -> ExportOut:
    """Per-locale name/subtitle/keywords/promotional_text/description as JSON.
    ``proposal`` overlays the last ``listing_localize`` result on the current listing."""
    return await _with_service(app_id, lambda s: s.export(source))
