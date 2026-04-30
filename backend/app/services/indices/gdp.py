import logging
from datetime import date

import httpx

from app.services.indices.base import IndexFetcher, IndexRecord

logger = logging.getLogger(__name__)

# World Bank uses ISO alpha-2 codes that mostly match Apple territory codes.
_WB_TO_APPLE: dict[str, str] = {
    "UK": "GB",
}

# Countries World Bank exposes only via non-standard codes (e.g. XKX for Kosovo).
_WB_SKIP_CODES: set[str] = {
    "XK",
}


class GDPFetcher(IndexFetcher):
    """Fetch raw GDP per capita (PPP, current international $) from World Bank.

    Stores raw USD values (not US-normalized) since the bracket calculator
    needs absolute figures for threshold comparisons.
    """

    index_type = "gdp_per_capita_ppp"
    WORLD_BANK_URL = "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.PCAP.PP.CD"
    TIMEOUT_SECONDS = 30

    async def fetch(self) -> list[IndexRecord]:
        try:
            raw_data = await self._fetch_raw()
        except Exception:
            logger.exception("Failed to fetch GDP/capita PPP from World Bank API")
            return []

        return self._parse(raw_data)

    async def _fetch_raw(self) -> list[dict]:
        all_records: list[dict] = []
        page = 1
        total_pages = 1

        async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
            while page <= total_pages:
                params = {
                    "format": "json",
                    "per_page": 300,
                    "date": "2023",
                    "page": page,
                }
                response = await client.get(self.WORLD_BANK_URL, params=params)
                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, list) or len(payload) < 2:
                    logger.warning("Unexpected World Bank API response format")
                    break

                metadata, records = payload[0], payload[1]
                total_pages = metadata.get("pages", 1)
                if records:
                    all_records.extend(records)
                page += 1

        return all_records

    def _parse(self, raw_data: list[dict]) -> list[IndexRecord]:
        results: list[IndexRecord] = []
        seen: set[str] = set()

        for record in raw_data:
            value = record.get("value")
            if value is None:
                continue

            wb_code = record.get("country", {}).get("id", "")
            if len(wb_code) != 2 or wb_code in _WB_SKIP_CODES:
                continue

            territory_code = _WB_TO_APPLE.get(wb_code, wb_code)
            if territory_code in seen:
                continue
            seen.add(territory_code)

            try:
                ref_date = date(int(record.get("date", "2023")), 1, 1)
            except (ValueError, TypeError):
                ref_date = date(2023, 1, 1)

            results.append(IndexRecord(
                territory_code=territory_code,
                value=round(float(value), 2),
                reference_date=ref_date,
            ))

        logger.info("GDP fetcher: parsed %d territory records", len(results))
        return results
