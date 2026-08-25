"""REST endpoints for the keyword-intelligence subsystem.

Two routes today:

* ``POST /apps/{app_id}/keyword-intel/refresh`` — runs the configured providers
  (currently ``asa_search_terms`` + ``asa_recommendations``) and writes results
  to the cache. Idempotent.
* ``GET /apps/{app_id}/keyword-intel`` — reads the cache, optionally filtered
  by keyword(s), locale, or source.

Both bodies live in ``app.services.keyword_intel.service`` so the MCP mirror
(``keyword_intel_list`` / ``keyword_intel_refresh_providers``) shares them.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.keyword_intel import (
    KeywordIntelOut,
    KeywordIntelRefreshOut,
)
from app.services.keyword_intel.service import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    MAX_DAYS,
    MAX_LIMIT,
    list_intel,
    run_providers,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/{app_id}/keyword-intel/refresh",
    response_model=KeywordIntelRefreshOut,
    status_code=status.HTTP_200_OK,
)
async def refresh_keyword_intel(
    app_id: int,
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KeywordIntelRefreshOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    return await run_providers(session, app_id, days=days)


@router.get(
    "/{app_id}/keyword-intel",
    response_model=list[KeywordIntelOut],
)
async def list_keyword_intel(
    app_id: int,
    keyword: list[str] | None = Query(
        default=None,
        description=(
            "Repeat to look up multiple keywords in one call: ?keyword=foo&keyword=bar"
        ),
    ),
    locale: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[KeywordIntelOut]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    return await list_intel(
        session,
        app_id,
        keywords=keyword,
        locale=locale,
        source=source,
        limit=limit,
    )
