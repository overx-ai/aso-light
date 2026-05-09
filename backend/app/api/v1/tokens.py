from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_session
from app.models.personal_access_token import PersonalAccessToken
from app.schemas.tokens import PATCreateRequest, PATCreateResponse, PATListItem

router = APIRouter()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_plaintext_token() -> str:
    return f"aso_pat_{secrets.token_urlsafe(32)}"


@router.get("", response_model=list[PATListItem])
async def list_tokens(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PATListItem]:
    result = await session.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == int(current_user["user_id"]))
        .order_by(PersonalAccessToken.created_at.desc())
    )
    rows = result.scalars().all()
    return [PATListItem.model_validate(row) for row in rows]


@router.post("", response_model=PATCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: PATCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PATCreateResponse:
    token = _issue_plaintext_token()
    row = PersonalAccessToken(
        user_id=int(current_user["user_id"]),
        name=body.name.strip(),
        token_hash=_hash_token(token),
    )
    session.add(row)
    await session.flush()
    return PATCreateResponse(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        token=token,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    result = await session.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id,
            PersonalAccessToken.user_id == int(current_user["user_id"]),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    row.revoked_at = datetime.now(timezone.utc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
