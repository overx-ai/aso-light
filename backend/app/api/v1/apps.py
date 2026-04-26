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

    from app.services.asc.client import ASCClient
    from app.services.asc.apps import ASCAppsService
    from app.services.asc.errors import ASCAPIError

    # Determine which credentials to sync
    if credential_id is not None:
        credentials = [credential]
    else:
        cred_result = await session.execute(
            select(ASCCredential).where(ASCCredential.user_id == user_id)
        )
        credentials = list(cred_result.scalars().all())

    if not credentials:
        return AppSyncResponse(synced=0, apps=[])

    synced_apps: list[App] = []

    for cred in credentials:
        try:
            async with ASCClient.from_credential(cred) as client:
                apps_service = ASCAppsService(client)
                apps_data = await apps_service.list_apps()

                for app_data in apps_data:
                    asc_app_id = app_data["id"]
                    attrs = app_data.get("attributes", {})

                    # Check if app already exists
                    existing = await session.execute(
                        select(App).where(
                            App.credential_id == cred.id,
                            App.asc_app_id == asc_app_id,
                        )
                    )
                    app_record = existing.scalar_one_or_none()

                    platform_raw = attrs.get("platform", "IOS")
                    platform = "ios" if platform_raw == "IOS" else "macos"

                    if app_record:
                        app_record.name = attrs.get("name", app_record.name)
                        app_record.bundle_id = attrs.get("bundleId", app_record.bundle_id)
                        app_record.platform = platform
                    else:
                        app_record = App(
                            credential_id=cred.id,
                            asc_app_id=asc_app_id,
                            bundle_id=attrs.get("bundleId", ""),
                            name=attrs.get("name", "Unknown"),
                            platform=platform,
                        )
                        session.add(app_record)

                    await session.flush()
                    synced_apps.append(app_record)

        except ASCAPIError as exc:
            logger.warning("ASC API error syncing credential id=%s: %s", cred.id, exc.message)
            continue

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
