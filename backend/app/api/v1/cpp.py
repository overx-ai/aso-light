"""Per-app Custom Product Page (CPP) REST endpoints.

Mounted under /apps so the auth chain runs through ``_get_verified_app``
identically to the other per-app routers (pricing, metadata, screenshots,
etc.). These are thin wrappers over
:class:`app.services.asc.cpp.ASCCustomProductPageService` — the same service
backing the ``cpp.*`` MCP tools — exposing the read + create surface the
React Compare page needs to populate its CPP picker and create new pages.

The composited BEFORE/AFTER montage itself is served by the sibling
``screenshots`` router (``GET /{app_id}/screenshots/compare``).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.cpp import (
    MAX_SCREENSHOT_BYTES,
    MAX_SCREENSHOT_FILES,
    CPPCreateIn,
    CPPFromUploadResponse,
    CPPListResponse,
    CPPResponse,
    is_valid_display_type,
)
from app.services.asc.cpp import ASCCustomProductPageService
from app.services.asc.errors import ASCAPIError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cpp"])


@asynccontextmanager
async def _asc_502() -> AsyncIterator[None]:
    """Translate an ``ASCAPIError`` raised within the block into a 502.

    Keeps raw Apple/Python errors out of API responses: every CPP route
    funnels its ASC call through this so the failure surfaces as a single
    ``502 Bad Gateway`` with Apple's message, never a traceback.
    """
    try:
        yield
    except ASCAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ASC API error: {exc.message}",
        ) from exc


def _to_cpp_response(resource: dict) -> CPPResponse:
    """Shape a raw ``appCustomProductPages`` resource into a CPPResponse."""
    attrs = resource.get("attributes", {})
    return CPPResponse(
        id=resource.get("id", ""),
        name=attrs.get("name"),
        visible=attrs.get("visible"),
    )


@router.get("/{app_id}/cpps", response_model=CPPListResponse)
async def list_cpps(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CPPListResponse:
    """List the Custom Product Pages for an app."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCCustomProductPageService(client)
        async with _asc_502():
            resources = await service.list_cpps(app.asc_app_id)
    return CPPListResponse(items=[_to_cpp_response(r) for r in resources])


@router.get("/{app_id}/cpps/{cpp_id}", response_model=CPPResponse)
async def get_cpp(
    app_id: int,
    cpp_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CPPResponse:
    """Fetch a single Custom Product Page by its ASC id."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCCustomProductPageService(client)
        async with _asc_502():
            resource = await service.get_cpp(cpp_id)
    return _to_cpp_response(resource)


@router.post(
    "/{app_id}/cpps",
    response_model=CPPResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cpp(
    app_id: int,
    body: CPPCreateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CPPResponse:
    """Create a Custom Product Page (Apple auto-creates a draft version)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCCustomProductPageService(client)
        async with _asc_502():
            resource = await service.create_cpp(
                app.asc_app_id, body.name, visible=body.visible,
            )
    return _to_cpp_response(resource)


async def _read_upload_payload(
    files: list[UploadFile],
) -> list[tuple[str, bytes]]:
    """Validate and buffer the uploaded screenshot set.

    Enforces the file-count and per-file size caps, drops empty parts, and
    returns ordered ``(file_name, file_bytes)`` tuples ready for the ASC
    upload. Raises a 400 ``HTTPException`` on any violation.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one screenshot file is required.",
        )
    if len(files) > MAX_SCREENSHOT_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_SCREENSHOT_FILES} screenshots are allowed.",
        )

    payload: list[tuple[str, bytes]] = []
    for upload in files:
        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{upload.filename or 'screenshot'}: exceeds the "
                    f"{MAX_SCREENSHOT_BYTES // (1024 * 1024)} MB limit."
                ),
            )
        payload.append((upload.filename or "screenshot.png", data))

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one non-empty screenshot file is required.",
        )
    return payload


@router.post(
    "/{app_id}/cpps/from-upload",
    response_model=CPPFromUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cpp_from_upload(
    app_id: int,
    name: str = Form(..., min_length=1, max_length=100),
    locale: str = Form(...),
    display_type: str = Form(...),
    files: list[UploadFile] = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CPPFromUploadResponse:
    """Create a Custom Product Page from an uploaded "after" screenshot set.

    Multipart/form-data: ``name``, ``locale``, ``display_type``, and repeated
    ``files``. The service creates the page (Apple auto-creates a draft
    version), ensures a localization for ``locale`` exists under that version,
    then uploads each file into the ``appScreenshotSet`` for ``display_type``
    via the 3-step reserve -> PUT -> commit flow.

    The created page can then be attached to Apple Search Ads ad groups.
    """
    if not is_valid_display_type(display_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown display_type '{display_type}'.",
        )
    payload = await _read_upload_payload(files)

    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    async with await _get_asc_client_for_app(app, session) as client:
        service = ASCCustomProductPageService(client)
        async with _asc_502():
            try:
                result = await service.create_cpp_with_screenshots(
                    app.asc_app_id, name, locale, display_type, payload,
                )
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc

    return CPPFromUploadResponse(
        cpp_id=result["cpp_id"],
        name=result.get("name"),
        uploaded_count=result["uploaded_count"],
    )
