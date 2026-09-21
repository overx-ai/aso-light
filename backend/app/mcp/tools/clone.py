"""MCP tools for reading and retrying clone operations.

``swap_iap`` / ``swap_subscription_product`` and the clone endpoints kick off
long multi-step operations. Without these an agent can start a swap and then
go blind — it has no way to read the per-step status or resume a partial
failure. Each tool delegates to the REST handler so the ownership gate and
the retry semantics stay in one place.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.api.v1.clone import (
    get_clone_operation,
    list_clone_operations,
    retry_clone_operation,
)
from app.mcp.context import (
    current_user_claims,
    http_to_tool_error,
    resolve_app,
    session_scope,
)
from app.mcp.server import mcp
from app.schemas.clone import CloneOperationOut


@mcp.tool(name="clone_list_operations")
async def clone_list_operations(app_id: int) -> list[CloneOperationOut]:
    """List the app's 100 most recent clone/swap operations, newest first."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        try:
            return await list_clone_operations(
                app_id=app.id,
                current_user=current_user_claims(),
                session=session,
            )
        except HTTPException as exc:
            raise http_to_tool_error(exc) from exc


@mcp.tool(name="clone_get_operation")
async def clone_get_operation(app_id: int, op_id: int) -> CloneOperationOut:
    """Read one clone/swap operation — per-step status and any ASC errors."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        try:
            return await get_clone_operation(
                app_id=app.id, op_id=op_id,
                current_user=current_user_claims(), session=session,
            )
        except HTTPException as exc:
            raise http_to_tool_error(exc) from exc


@mcp.tool(name="clone_retry_operation")
async def clone_retry_operation(app_id: int, op_id: int) -> CloneOperationOut:
    """Re-run a clone/swap for the same target productId.

    Idempotent — the cloners detect already-created ASC objects and skip
    successful steps, so this is safe after a partial failure. Ungated by
    design: it resumes an operation the user already consented to.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        try:
            return await retry_clone_operation(
                app_id=app.id, op_id=op_id,
                current_user=current_user_claims(), session=session,
            )
        except HTTPException as exc:
            raise http_to_tool_error(exc) from exc
