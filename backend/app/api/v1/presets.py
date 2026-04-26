"""Price presets CRUD API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_session
from app.models.preset import PricePreset
from app.schemas.preset import PresetCreate, PresetResponse, PresetUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _get_user_preset(
    preset_id: int,
    user_id: int,
    session: AsyncSession,
) -> PricePreset:
    """Load a preset and verify ownership.

    Raises HTTPException 404 when the preset does not exist or does not
    belong to the requesting user.
    """
    result = await session.execute(
        select(PricePreset).where(
            PricePreset.id == preset_id,
            PricePreset.user_id == user_id,
        )
    )
    preset = result.scalar_one_or_none()
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preset not found",
        )
    return preset


# ------------------------------------------------------------------
# CRUD endpoints
# ------------------------------------------------------------------


@router.get("", response_model=list[PresetResponse])
async def list_presets(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PresetResponse]:
    """List all price presets belonging to the current user."""
    user_id = int(current_user["user_id"])
    result = await session.execute(
        select(PricePreset)
        .where(PricePreset.user_id == user_id)
        .order_by(PricePreset.created_at.desc())
    )
    presets = result.scalars().all()
    return [PresetResponse.model_validate(p) for p in presets]


@router.post("", response_model=PresetResponse, status_code=status.HTTP_201_CREATED)
async def create_preset(
    body: PresetCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    """Create a new price preset for the current user."""
    user_id = int(current_user["user_id"])
    preset = PricePreset(
        user_id=user_id,
        name=body.name,
        base_territory_code=body.base_territory_code,
        base_price=body.base_price,
        index_type=body.index_type,
        apply_vat=body.apply_vat,
        charming_mode=body.charming_mode,
    )
    session.add(preset)
    await session.flush()
    await session.refresh(preset)
    return PresetResponse.model_validate(preset)


@router.get("/{preset_id}", response_model=PresetResponse)
async def get_preset(
    preset_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    """Get a single preset by ID (owned by the current user)."""
    user_id = int(current_user["user_id"])
    preset = await _get_user_preset(preset_id, user_id, session)
    return PresetResponse.model_validate(preset)


@router.put("/{preset_id}", response_model=PresetResponse)
async def update_preset(
    preset_id: int,
    body: PresetUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    """Update an existing preset (partial update, only provided fields)."""
    user_id = int(current_user["user_id"])
    preset = await _get_user_preset(preset_id, user_id, session)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(preset, field, value)

    await session.flush()
    await session.refresh(preset)
    return PresetResponse.model_validate(preset)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_preset(
    preset_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a preset owned by the current user."""
    user_id = int(current_user["user_id"])
    preset = await _get_user_preset(preset_id, user_id, session)
    await session.delete(preset)
    await session.flush()
