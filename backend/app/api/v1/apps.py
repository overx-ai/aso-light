import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_session
from app.models.app import App
from app.models.credential import ASCCredential
from app.schemas.app import AppResponse, AppSyncResponse

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_user_credential_ids(
    session: AsyncSession,
    user_id: int,
) -> list[int]:
    result = await session.execute(
        select(ASCCredential.id).where(ASCCredential.user_id == user_id)
    )
    return list(result.scalars().all())


@router.get("", response_model=list[AppResponse])
async def list_apps(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AppResponse]:
    user_id = int(current_user["user_id"])
    credential_ids = await _get_user_credential_ids(session, user_id)
    if not credential_ids:
        return []

    result = await session.execute(
        select(App).where(App.credential_id.in_(credential_ids))
    )
    apps = result.scalars().all()
    return [AppResponse.model_validate(app) for app in apps]


@router.post("/sync", response_model=AppSyncResponse)
async def sync_apps(
    credential_id: int | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppSyncResponse:
    user_id = int(current_user["user_id"])

    if credential_id is not None:
        result = await session.execute(
            select(ASCCredential).where(ASCCredential.id == credential_id)
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        if credential.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this credential",
            )

    from app.services.asc.apps import sync_apps_for_credentials
    from app.services.keywords.itunes_search import ITunesSearchService

    if credential_id is not None:
        credentials = [credential]
    else:
        cred_result = await session.execute(
            select(ASCCredential).where(ASCCredential.user_id == user_id)
        )
        credentials = list(cred_result.scalars().all())

    if not credentials:
        return AppSyncResponse(synced=0, apps=[])

    synced_apps = await sync_apps_for_credentials(session, credentials)
    if synced_apps:
        await ITunesSearchService().backfill_icons(synced_apps)
        await session.flush()

    logger.info("Synced %d app(s) for user_id=%s", len(synced_apps), user_id)
    return AppSyncResponse(
        synced=len(synced_apps),
        apps=[AppResponse.model_validate(a) for a in synced_apps],
    )


@router.get("/{app_id}", response_model=AppResponse)
async def get_app(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppResponse:
    user_id = int(current_user["user_id"])

    result = await session.execute(select(App).where(App.id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found",
        )

    credential_ids = await _get_user_credential_ids(session, user_id)
    if app.credential_id not in credential_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this app",
        )

    return AppResponse.model_validate(app)
