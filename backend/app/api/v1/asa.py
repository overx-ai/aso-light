"""REST endpoints for ASA credentials, orgs, and sync.

Per-app endpoints (campaigns, reports, search terms, negatives) live in
asa_app.py and are mounted under /apps. This file owns the
credential-scoped surface only.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_value, get_current_user
from app.db.session import get_session
from app.models.asa import ASACredential, ASAOrg, ASASyncOperation
from app.schemas.asa import (
    ASACredentialCreate,
    ASACredentialOut,
    ASAOrgOut,
    ASASyncOperationOut,
    ASATestResult,
)
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.sync import run_sync

logger = logging.getLogger(__name__)
router = APIRouter()


async def _own_credential(
    credential_id: int, user_id: int, session: AsyncSession,
) -> ASACredential:
    res = await session.execute(
        select(ASACredential).where(
            ASACredential.id == credential_id,
            ASACredential.user_id == user_id,
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ASA credential not found",
        )
    return cred


@router.get("/credentials", response_model=list[ASACredentialOut])
async def list_credentials(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASACredentialOut]:
    user_id = int(current_user["user_id"])
    res = await session.execute(
        select(ASACredential)
        .where(ASACredential.user_id == user_id)
        .order_by(ASACredential.created_at.desc())
    )
    return [ASACredentialOut.model_validate(c) for c in res.scalars().all()]


@router.post(
    "/credentials",
    response_model=ASACredentialOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    body: ASACredentialCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASACredentialOut:
    """Create + immediately validate against Apple before persisting."""
    user_id = int(current_user["user_id"])
    cred = ASACredential(
        user_id=user_id,
        name=body.name,
        client_id_ciphertext=encrypt_value(body.client_id),
        team_id_ciphertext=encrypt_value(body.team_id),
        key_id=body.key_id,
        private_key_ciphertext=encrypt_value(body.private_key_pem),
    )
    session.add(cred)
    await session.flush()
    # Validate against Apple immediately — reject the cred if /me/acl fails.
    try:
        client = await ASAClient.from_credential(cred)
        try:
            await client.request("GET", "/me/acl")
        finally:
            await client.aclose()
    except ASAAPIError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ASA credential rejected by Apple: {exc.message}",
        )
    await session.refresh(cred)
    logger.info(
        "ASA cred created: id=%s user=%s name=%s",
        cred.id, user_id, body.name,
    )
    return ASACredentialOut.model_validate(cred)


@router.delete(
    "/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    cred = await _own_credential(credential_id, user_id, session)
    await session.delete(cred)
    logger.info("ASA cred deleted: id=%s user=%s", credential_id, user_id)


@router.post(
    "/credentials/{credential_id}/test",
    response_model=ASATestResult,
)
async def test_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASATestResult:
    user_id = int(current_user["user_id"])
    cred = await _own_credential(credential_id, user_id, session)
    try:
        client = await ASAClient.from_credential(cred)
        try:
            payload = await client.request("GET", "/me/acl")
        finally:
            await client.aclose()
    except ASAAPIError as exc:
        return ASATestResult(ok=False, orgs_visible=0, detail=exc.message)
    return ASATestResult(
        ok=True,
        orgs_visible=len(payload.get("data") or []),
    )


@router.get(
    "/credentials/{credential_id}/orgs",
    response_model=list[ASAOrgOut],
)
async def list_orgs(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASAOrgOut]:
    user_id = int(current_user["user_id"])
    await _own_credential(credential_id, user_id, session)
    res = await session.execute(
        select(ASAOrg).where(ASAOrg.credential_id == credential_id)
    )
    return [ASAOrgOut.model_validate(o) for o in res.scalars().all()]


@router.post(
    "/credentials/{credential_id}/sync",
    response_model=ASASyncOperationOut,
)
async def sync_credential(
    credential_id: int,
    full: bool = False,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASASyncOperationOut:
    user_id = int(current_user["user_id"])
    await _own_credential(credential_id, user_id, session)
    op = await run_sync(
        session=session,
        credential_id=credential_id,
        user_id=user_id,
        full_backfill=full,
    )
    return ASASyncOperationOut(
        id=op.id,
        credential_id=op.credential_id,
        status=op.status,
        full_backfill=op.full_backfill,
        steps=op.steps or [],
        error_log=op.error_log or [],
        started_at=op.started_at,
        completed_at=op.completed_at,
    )
