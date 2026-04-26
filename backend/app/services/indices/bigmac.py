import csv
import io
import logging
from datetime import date

import httpx

from app.services.indices.base import IndexFetcher, IndexRecord

logger = logging.getLogger(__name__)

# The Economist Big Mac data uses ISO alpha-3 codes; map to ISO alpha-2
# (Apple territory codes).
_ISO3_TO_ISO2: dict[str, str] = {
    "ARG": "AR", "AUS": "AU", "AZE": "AZ", "BHR": "BH", "BRA": "BR",
    "GBR": "GB", "CAN": "CA", "CHL": "CL", "CHN": "CN", "COL": "CO",
    "CRI": "CR", "CZE": "CZ", "DNK": "DK", "EGY": "EG", "HKG": "HK",
    "HUN": "HU", "IDN": "ID", "IND": "IN", "ISR": "IL", "JPN": "JP",
    "JOR": "JO", "KOR": "KR", "KWT": "KW", "LBN": "LB", "LKA": "LK",
    "MYS": "MY", "MEX": "MX", "MDA": "MD", "NZL": "NZ", "NIC": "NI",
    "NOR": "NO", "OMN": "OM", "PAK": "PK", "PAN": "PA", "PER": "PE",
    "PHL": "PH", "POL": "PL", "QAT": "QA", "ROU": "RO", "RUS": "RU",
    "SAU": "SA", "SGP": "SG", "ZAF": "ZA", "SWE": "SE", "CHE": "CH",
    "TWN": "TW", "THA": "TH", "TUR": "TR", "ARE": "AE", "UKR": "UA",
    "URY": "UY", "USA": "US", "VNM": "VN", "VEN": "VE", "PRT": "PT",
    "ESP": "ES", "GRC": "GR", "NLD": "NL", "BEL": "BE", "AUT": "AT",
    "FIN": "FI", "IRL": "IE", "ITA": "IT", "DEU": "DE", "FRA": "FR",
    "EST": "EE", "LVA": "LV", "LTU": "LT", "SVK": "SK", "SVN": "SI",
    "HRV": "HR", "BGD": "BD", "GTM": "GT", "HND": "HN",
}


class BigMacFetcher(IndexFetcher):
    """Fetch Big Mac Index data from The Economist's GitHub repository."""

    index_type = "bigmac"
    CSV_URL = (
        "https://raw.githubusercontent.com/TheEconomist/"
        "big-mac-data/master/output-data/big-mac-raw-index.csv"
    )
    TIMEOUT_SECONDS = 30

    async def fetch(self) -> list[IndexRecord]:
        try:
            csv_text = await self._fetch_csv()
        except Exception:
            logger.exception("Failed to fetch Big Mac CSV data")
            return []

        return self._parse(csv_text)

    async def _fetch_csv(self) -> str:
        async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
            response = await client.get(self.CSV_URL)
            response.raise_for_status()
            return response.text

    def _parse(self, csv_text: str) -> list[IndexRecord]:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        if not rows:
            logger.warning("Big Mac CSV is empty")
            return []

        # Find the latest date in the dataset
        all_dates: set[str] = set()
        for row in rows:
            date_str = row.get("date", "")
            if date_str:
                all_dates.add(date_str)

        if not all_dates:
            logger.warning("No valid dates found in Big Mac data")
            return []

        latest_date_str = max(all_dates)
        try:
            parts = latest_date_str.split("-")
            ref_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            ref_date = date(2023, 7, 1)

        # Filter to latest date only
        latest_rows = [r for r in rows if r.get("date") == latest_date_str]

        # Extract dollar prices
        prices_by_code: dict[str, float] = {}
        for row in latest_rows:
            iso3 = row.get("iso_a3", "")
            dollar_price = row.get("dollar_price")
            if not iso3 or dollar_price is None:
                continue

            try:
                dollar_price_f = float(dollar_price)
            except (ValueError, TypeError):
                continue

            iso2 = _ISO3_TO_ISO2.get(iso3)
            if iso2:
                prices_by_code[iso2] = dollar_price_f

        # Calculate multipliers relative to US
        us_price = prices_by_code.get("US")
        if us_price is None or us_price == 0:
            logger.warning("US Big Mac price not found or zero")
            return []

        results: list[IndexRecord] = []
        for code, price in prices_by_code.items():
            multiplier = price / us_price
            results.append(IndexRecord(
                territory_code=code,
                value=round(multiplier, 6),
                reference_date=ref_date,
            ))

        logger.info("Big Mac fetcher: parsed %d territory records", len(results))
        return results
