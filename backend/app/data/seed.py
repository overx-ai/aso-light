import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.territories import TERRITORIES
from app.models.territory import Territory

logger = logging.getLogger(__name__)


async def seed_territories(session: AsyncSession) -> None:
    """Insert or update all Apple App Store territories.

    Uses a select-then-merge pattern to be fully idempotent: safe to call on
    every application startup without duplicating rows.
    """
    existing_result = await session.execute(select(Territory.code, Territory.id))
    code_to_id: dict[str, int] = {row.code: row.id for row in existing_result}

    inserted = 0
    updated = 0

    for data in TERRITORIES:
        gdp = data.get("gdp_per_capita_usd")
        territory_id = code_to_id.get(data["code"])
        if territory_id is not None:
            territory = await session.get(Territory, territory_id)
            if territory is None:
                continue
            territory.name = data["name"]
            territory.currency_code = data["currency_code"]
            territory.vat_rate = data["vat_rate"]
            # Only backfill GDP when DB column is empty — avoid clobbering
            # any future operator override of seed values.
            if territory.gdp_per_capita_usd is None and gdp is not None:
                territory.gdp_per_capita_usd = gdp
            updated += 1
        else:
            territory = Territory(
                code=data["code"],
                name=data["name"],
                currency_code=data["currency_code"],
                vat_rate=data["vat_rate"],
                gdp_per_capita_usd=gdp,
                is_active=True,
            )
            session.add(territory)
            inserted += 1

    await session.commit()
    logger.info(
        "Territory seed complete: %d inserted, %d updated, %d total",
        inserted, updated, len(TERRITORIES),
    )
