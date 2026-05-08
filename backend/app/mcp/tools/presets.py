"""MCP tools for price-preset CRUD."""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.context import get_user_id, session_scope
from app.mcp.server import mcp
from app.models.preset import PricePreset
from app.schemas.preset import PresetCreate, PresetResponse, PresetUpdate


async def _get_user_preset(
    preset_id: int, user_id: int, session: AsyncSession
) -> PricePreset:
    res = await session.execute(
        select(PricePreset).where(
            PricePreset.id == preset_id,
            PricePreset.user_id == user_id,
        )
    )
    preset = res.scalar_one_or_none()
    if preset is None:
        raise ToolError("Preset not found")
    return preset


@mcp.tool(name="presets.list")
async def list_presets_tool() -> list[PresetResponse]:
    """List all price presets owned by the authenticated user."""
    async with session_scope() as session:
        user_id = get_user_id()
        res = await session.execute(
            select(PricePreset)
            .where(PricePreset.user_id == user_id)
            .order_by(PricePreset.created_at.desc())
        )
        return [PresetResponse.model_validate(p) for p in res.scalars().all()]


@mcp.tool(name="presets.create")
async def create_preset_tool(body: PresetCreate) -> PresetResponse:
    """Create a new price preset for the current user."""
    async with session_scope() as session:
        user_id = get_user_id()
        preset = PricePreset(
            user_id=user_id,
            name=body.name,
            base_territory_code=body.base_territory_code,
            base_price=body.base_price,
            index_type=body.index_type,
            apply_vat=body.apply_vat,
            charming_mode=body.charming_mode,
            config=body.config,
        )
        session.add(preset)
        await session.flush()
        await session.refresh(preset)
        return PresetResponse.model_validate(preset)


@mcp.tool(name="presets.get")
async def get_preset_tool(preset_id: int) -> PresetResponse:
    """Fetch a single preset by id (must be owned by the current user)."""
    async with session_scope() as session:
        user_id = get_user_id()
        preset = await _get_user_preset(preset_id, user_id, session)
        return PresetResponse.model_validate(preset)


@mcp.tool(name="presets.update")
async def update_preset_tool(preset_id: int, body: PresetUpdate) -> PresetResponse:
    """Partial-update an existing preset; only provided fields are written."""
    async with session_scope() as session:
        user_id = get_user_id()
        preset = await _get_user_preset(preset_id, user_id, session)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(preset, field, value)
        await session.flush()
        await session.refresh(preset)
        return PresetResponse.model_validate(preset)


@mcp.tool(name="presets.delete")
async def delete_preset_tool(preset_id: int) -> dict[str, bool]:
    """Delete a preset owned by the current user."""
    async with session_scope() as session:
        user_id = get_user_id()
        preset = await _get_user_preset(preset_id, user_id, session)
        await session.delete(preset)
        await session.flush()
        return {"deleted": True}
