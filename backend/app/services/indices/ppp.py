import logging
from datetime import date

import httpx

from app.services.indices.base import IndexFetcher, IndexRecord

logger = logging.getLogger(__name__)

# World Bank API uses ISO alpha-2 codes which generally match Apple territory
# codes. This mapping handles exceptions where they differ.
_WB_TO_APPLE: dict[str, str] = {
    "UK": "GB",  # World Bank uses UK for United Kingdom
}

# Countries where World Bank uses alpha-3 or non-standard codes
_WB_SKIP_CODES: set[str] = {
    "XK",  # Kosovo - World Bank uses XKX
}


class PPPFetcher(IndexFetcher):
    """Fetch PPP conversion factors from the World Bank API."""

    index_type = "ppp"
    WORLD_BANK_URL = "https://api.worldbank.org/v2/country/all/indicator/PA.NUS.PPP"
    TIMEOUT_SECONDS = 30

    async def fetch(self) -> list[IndexRecord]:
        try:
            raw_data = await self._fetch_raw()
        except Exception:
            logger.exception("Failed to fetch PPP data from World Bank API")
            return []

        return self._parse(raw_data)

    async def _fetch_raw(self) -> list[dict]:
        """Fetch all pages of PPP data from the World Bank API."""
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

                metadata = payload[0]
                records = payload[1]

                total_pages = metadata.get("pages", 1)
                if records:
                    all_records.extend(records)
                page += 1

        return all_records

    def _parse(self, raw_data: list[dict]) -> list[IndexRecord]:
        """Parse World Bank response and calculate PPP multipliers relative to US."""
        # First pass: extract PPP values by country code
        ppp_by_code: dict[str, tuple[float, str]] = {}
        for record in raw_data:
            value = record.get("value")
            if value is None:
                continue

            country_info = record.get("country", {})
            country_code = record.get("countryiso3code", "")

            # World Bank returns ISO alpha-3 codes in countryiso3code
            # but also has a 2-letter id. We extract the 2-letter code.
            wb_code = country_info.get("id", "")
            if not wb_code or len(wb_code) != 2:
                continue

            ref_date_str = record.get("date", "2023")
            try:
                ref_date = date(int(ref_date_str), 1, 1)
            except (ValueError, TypeError):
                ref_date = date(2023, 1, 1)

            # Map World Bank code to Apple territory code
            territory_code = _WB_TO_APPLE.get(wb_code, wb_code)
            ppp_by_code[territory_code] = (float(value), ref_date_str)

        # Get US PPP value as the base
        us_entry = ppp_by_code.get("US")
        if us_entry is None:
            logger.warning("US PPP value not found in World Bank data")
            return []

        us_ppp = us_entry[0]
        if us_ppp == 0:
            logger.warning("US PPP value is zero, cannot normalize")
            return []

        # Calculate multipliers relative to US
        results: list[IndexRecord] = []
        for code, (ppp_value, date_str) in ppp_by_code.items():
            if code in _WB_SKIP_CODES:
                continue

            try:
                ref_date = date(int(date_str), 1, 1)
            except (ValueError, TypeError):
                ref_date = date(2023, 1, 1)

            multiplier = ppp_value / us_ppp
            results.append(IndexRecord(
                territory_code=code,
                value=round(multiplier, 6),
                reference_date=ref_date,
            ))

        logger.info("PPP fetcher: parsed %d territory records", len(results))
        return results
