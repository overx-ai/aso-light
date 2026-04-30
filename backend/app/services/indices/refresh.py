import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.economic_index import EconomicIndex
from app.models.territory import Territory
from app.services.indices.base import IndexFetcher, IndexRecord
from app.services.indices.bigmac import BigMacFetcher
from app.services.indices.gdp import GDPFetcher
from app.services.indices.netflix import NetflixFetcher
from app.services.indices.ppp import PPPFetcher
from app.services.indices.spotify import SpotifyFetcher

logger = logging.getLogger(__name__)


class IndexRefreshService:
    """Orchestrates fetching and persisting all economic index data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.fetchers: list[IndexFetcher] = [
            PPPFetcher(),
            BigMacFetcher(),
            NetflixFetcher(),
            SpotifyFetcher(),
            GDPFetcher(),
        ]
        self._fetcher_map: dict[str, IndexFetcher] = {
            f.index_type: f for f in self.fetchers
        }

    async def refresh_all(self) -> dict[str, int]:
        """Refresh all indices. Returns count of records upserted per type."""
        results: dict[str, int] = {}
        for fetcher in self.fetchers:
            try:
                records = await fetcher.fetch()
                count = await self._save_records(fetcher.index_type, records)
                results[fetcher.index_type] = count
            except Exception:
                logger.exception(
                    "Error refreshing index type %s", fetcher.index_type,
                )
                results[fetcher.index_type] = 0
        return results

    async def refresh_type(self, index_type: str) -> int:
        """Refresh a specific index type. Returns count of records upserted."""
        fetcher = self._fetcher_map.get(index_type)
        if fetcher is None:
            raise ValueError(f"Unknown index type: {index_type}")

        records = await fetcher.fetch()
        return await self._save_records(index_type, records)

    async def _save_records(
        self,
        index_type: str,
        records: list[IndexRecord],
    ) -> int:
        """Save index records to DB, upserting by (territory_id, index_type)."""
        if not records:
            return 0

        # Build territory code -> id lookup
        result = await self.session.execute(
            select(Territory.code, Territory.id)
        )
        code_to_id: dict[str, int] = {row.code: row.id for row in result}

        # Load existing indices for this type to enable upsert
        existing_result = await self.session.execute(
            select(EconomicIndex).where(
                EconomicIndex.index_type == index_type,
            )
        )
        existing_by_territory: dict[int, EconomicIndex] = {
            idx.territory_id: idx
            for idx in existing_result.scalars().all()
        }

        upserted = 0
        for record in records:
            territory_id = code_to_id.get(record.territory_code)
            if territory_id is None:
                logger.debug(
                    "Skipping index record for unknown territory: %s",
                    record.territory_code,
                )
                continue

            existing = existing_by_territory.get(territory_id)
            if existing is not None:
                existing.value = record.value
                existing.reference_date = record.reference_date
            else:
                new_index = EconomicIndex(
                    territory_id=territory_id,
                    index_type=index_type,
                    value=record.value,
                    reference_date=record.reference_date,
                )
                self.session.add(new_index)

            upserted += 1

        await self.session.flush()
        logger.info(
            "Index refresh [%s]: upserted %d records", index_type, upserted,
        )
        return upserted
