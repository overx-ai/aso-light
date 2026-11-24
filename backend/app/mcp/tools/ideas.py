"""MCP access to the daily app-idea digest (spec 021)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Annotated

from pydantic import Field

from app.core.config import settings
from app.mcp.context import get_user_id
from app.mcp.server import mcp
from app.schemas.idea_digest import IdeaOut, IdeaStatus
from app.services.idea_digest import WEEK_DAYS, IdeaStore


@mcp.tool(name="idea_digest_list")
async def list_ideas(
    days: Annotated[int, Field(ge=1, le=365)] = WEEK_DAYS, status: IdeaStatus | None = None,
) -> list[IdeaOut]:
    """App ideas found by the daily digest in the last ``days`` days, newest first.

    ``status`` filters by decision, made in Studio.
    For an on-demand scan of your own topics use ``keyword_intel_niche_scan``. Read-only.
    """
    get_user_id()
    store = IdeaStore(Path(settings.IDEA_DIGEST_DIR))
    ideas = await asyncio.to_thread(store.recent, date.today(), days)
    return [i for i in ideas if status is None or i.status == status]
