import logging
from datetime import date

from app.services.indices.base import IndexFetcher, IndexRecord

logger = logging.getLogger(__name__)

# Netflix Standard plan prices in local currency (as of early 2024).
# The multiplier is pre-calculated as (local_price_in_usd / us_price).
# These approximate USD-equivalent ratios are based on exchange rates at the
# time of data collection. The raw local prices are kept as documentation.
#
# Format: territory_code -> multiplier (relative to US = 1.0)
# US Standard plan: $15.49
_NETFLIX_MULTIPLIERS: dict[str, float] = {
    "US": 1.000,
    "CA": 0.953,   # C$19.99
    "MX": 0.545,   # MXN 144.99
    "BR": 0.516,   # R$39.90
    "AR": 0.271,   # ARS 3599
    "CL": 0.548,   # CLP 8300
    "CO": 0.461,   # COP 29900
    "PE": 0.516,   # PEN 29.90
    "EC": 0.581,   # $9.00 (USD territory)
    "CR": 0.500,   # CRC 5400
    "PA": 0.581,   # $9.00
    "DO": 0.490,   # DOP 449
    "UY": 0.548,   # UYU 499
    "GT": 0.516,   # GTQ 59.99
    "HN": 0.490,   # HNL 179.99
    "GB": 0.906,   # GBP 10.99
    "DE": 0.965,   # EUR 13.99
    "FR": 0.965,   # EUR 13.99
    "IT": 0.965,   # EUR 13.99
    "ES": 0.858,   # EUR 12.99
    "PT": 0.823,   # EUR 11.99
    "NL": 0.965,   # EUR 13.99
    "BE": 0.965,   # EUR 13.99
    "AT": 0.965,   # EUR 13.99
    "CH": 1.032,   # CHF 15.95
    "LU": 0.965,   # EUR 13.99
    "IE": 0.965,   # EUR 13.99
    "SE": 0.913,   # SEK 139
    "NO": 0.942,   # NOK 139
    "DK": 0.942,   # DKK 99
    "FI": 0.965,   # EUR 13.99
    "IS": 0.852,   # ISK 1890
    "PL": 0.574,   # PLN 43
    "CZ": 0.548,   # CZK 279
    "HU": 0.500,   # HUF 3490
    "RO": 0.548,   # RON 45
    "BG": 0.500,   # BGN 15.99
    "HR": 0.548,   # EUR 7.99
    "SK": 0.823,   # EUR 11.99
    "SI": 0.823,   # EUR 11.99
    "EE": 0.823,   # EUR 11.99
    "LV": 0.823,   # EUR 11.99
    "LT": 0.823,   # EUR 11.99
    "GR": 0.823,   # EUR 11.99
    "RU": 0.435,   # RUB 599
    "UA": 0.258,   # UAH 299
    "KZ": 0.316,   # KZT 2200
    "TR": 0.355,   # TRY 99.99
    "IL": 0.810,   # ILS 44.90
    "SA": 0.645,   # SAR 39.99
    "AE": 0.677,   # AED 39.99
    "EG": 0.290,   # EGP 139
    "ZA": 0.503,   # ZAR 159
    "NG": 0.258,   # NGN 3600
    "KE": 0.387,   # KES 900
    "GH": 0.355,   # GHS 60
    "JP": 0.645,   # JPY 1490
    "KR": 0.735,   # KRW 13500
    "CN": 0.516,   # CNY 54
    "TW": 0.548,   # TWD 270
    "HK": 0.619,   # HKD 73
    "SG": 0.797,   # SGD 16.48
    "MY": 0.439,   # MYR 33
    "TH": 0.413,   # THB 219
    "VN": 0.316,   # VND 110000
    "PH": 0.387,   # PHP 369
    "ID": 0.387,   # IDR 94000
    "IN": 0.500,   # INR 649
    "PK": 0.258,   # PKR 700
    "BD": 0.226,   # BDT 300
    "LK": 0.258,   # LKR 800
    "AU": 0.903,   # AUD 22.99
    "NZ": 0.845,   # NZD 22.99
}

# Reference date for the seed data
_REFERENCE_DATE = date(2024, 1, 1)


class NetflixFetcher(IndexFetcher):
    """Netflix price index using hardcoded seed data.

    Netflix changes pricing frequently and has no stable public API,
    so we maintain a curated dataset of approximate price ratios.
    """

    index_type = "netflix"

    async def fetch(self) -> list[IndexRecord]:
        results: list[IndexRecord] = []
        for code, multiplier in _NETFLIX_MULTIPLIERS.items():
            results.append(IndexRecord(
                territory_code=code,
                value=multiplier,
                reference_date=_REFERENCE_DATE,
            ))

        logger.info("Netflix fetcher: returned %d territory records", len(results))
        return results
