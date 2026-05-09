from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.db.session import async_session_factory
from app.models.app import App

_current_user_id: ContextVar[int | None] = ContextVar("mcp_current_user_id", default=None)


def set_user_id(user_id: int) -> None:
    _current_user_id.set(user_id)


def get_user_id() -> int:
    user_id = _current_user_id.get()
    if user_id is None:
        raise ToolError("Missing MCP user context")
    return user_id


@asynccontextmanager
async def session_scope() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def resolve_app(app_id: int, session: AsyncSession) -> App:
    return await _get_verified_app(app_id, get_user_id(), session)


def _http_to_tool_error(exc: HTTPException) -> ToolError:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return ToolError(detail)
