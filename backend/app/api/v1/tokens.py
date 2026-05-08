import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_pat, get_current_user
from app.db.session import get_session
from app.models.personal_access_token import PersonalAccessToken
from app.schemas.personal_access_token import (
    PATCreateRequest,
    PATCreateResponse,
    PATListItem,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "",
    response_model=PATCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    body: PATCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PATCreateResponse:
    user_id = int(current_user["user_id"])
    plaintext, token_hash = generate_pat()
    pat = PersonalAccessToken(
        user_id=user_id,
        name=body.name,
        token_hash=token_hash,
    )
    session.add(pat)
    await session.flush()
    logger.info("PAT issued: id=%s user_id=%s name=%s", pat.id, user_id, body.name)
    return PATCreateResponse(
        id=pat.id,
        name=pat.name,
        token=plaintext,
        created_at=pat.created_at,
    )


@router.get("", response_model=list[PATListItem])
async def list_tokens(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PATListItem]:
    user_id = int(current_user["user_id"])
    res = await session.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user_id)
        .order_by(PersonalAccessToken.created_at.desc())
    )
    return [PATListItem.model_validate(row) for row in res.scalars().all()]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    res = await session.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id,
            PersonalAccessToken.user_id == user_id,
        )
    )
    pat = res.scalar_one_or_none()
    if pat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    if pat.revoked_at is None:
        pat.revoked_at = datetime.now(timezone.utc)
    logger.info("PAT revoked: id=%s user_id=%s", pat.id, user_id)
