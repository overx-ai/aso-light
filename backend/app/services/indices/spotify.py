import logging
from datetime import date

from app.services.indices.base import IndexFetcher, IndexRecord

logger = logging.getLogger(__name__)

# Spotify Individual plan prices expressed as multipliers relative to
# the US price ($11.99 = 1.0). Based on publicly available pricing data
# as of early 2024.
#
# Format: territory_code -> multiplier (local_price_usd_equiv / us_price)
_SPOTIFY_MULTIPLIERS: dict[str, float] = {
    "US": 1.000,
    "CA": 0.889,   # C$11.99 -> ~$8.87 USD (exchange rate dependent)
    "MX": 0.479,   # MXN 99
    "BR": 0.435,   # R$21.90
    "AR": 0.150,   # ARS 1549
    "CL": 0.441,   # CLP 5190
    "CO": 0.375,   # COP 16900
    "PE": 0.373,   # PEN 16.90
    "EC": 0.584,   # $6.99
    "CR": 0.375,   # CRC 3290
    "PA": 0.584,   # $6.99
    "DO": 0.375,   # DOP 279
    "UY": 0.417,   # UYU 289
    "GT": 0.375,   # GTQ 46.99
    "HN": 0.375,   # HNL 109
    "BO": 0.375,   # BOB 45.99
    "NI": 0.292,   # NIO 99
    "PY": 0.375,   # PYG 27900
    "SV": 0.584,   # $6.99
    "GB": 1.175,   # GBP 10.99
    "DE": 0.917,   # EUR 10.99
    "FR": 0.917,   # EUR 10.99
    "IT": 0.917,   # EUR 10.99
    "ES": 0.917,   # EUR 10.99
    "PT": 0.917,   # EUR 10.99
    "NL": 0.917,   # EUR 10.99
    "BE": 0.917,   # EUR 10.99
    "AT": 0.917,   # EUR 10.99
    "CH": 1.100,   # CHF 14.99
    "LU": 0.917,   # EUR 10.99
    "IE": 0.917,   # EUR 10.99
    "SE": 0.950,   # SEK 119
    "NO": 0.950,   # NOK 119
    "DK": 0.950,   # DKK 89
    "FI": 0.917,   # EUR 10.99
    "IS": 0.850,   # ISK 1499
    "PL": 0.500,   # PLN 23.99
    "CZ": 0.458,   # CZK 179
    "HU": 0.417,   # HUF 2290
    "RO": 0.417,   # RON 27.99
    "BG": 0.417,   # BGN 10.99
    "HR": 0.542,   # EUR 6.49
    "SK": 0.750,   # EUR 8.99
    "SI": 0.750,   # EUR 8.99
    "EE": 0.750,   # EUR 8.99
    "LV": 0.750,   # EUR 8.99
    "LT": 0.750,   # EUR 8.99
    "GR": 0.750,   # EUR 8.99
    "RU": 0.208,   # RUB 199
    "UA": 0.150,   # UAH 149
    "KZ": 0.200,   # KZT 999
    "TR": 0.242,   # TRY 59.99
    "IL": 0.667,   # ILS 29.90
    "SA": 0.542,   # SAR 26.99
    "AE": 0.542,   # AED 26.99
    "EG": 0.208,   # EGP 59.99
    "ZA": 0.417,   # ZAR 79.99
    "NG": 0.167,   # NGN 1800
    "KE": 0.250,   # KES 459
    "GH": 0.167,   # GHS 20
    "JP": 0.834,   # JPY 980
    "KR": 0.750,   # KRW 10900
    "CN": 0.250,   # CNY 18 (not officially available, estimate)
    "TW": 0.458,   # TWD 179
    "HK": 0.625,   # HKD 58
    "SG": 0.750,   # SGD 11.98
    "MY": 0.375,   # MYR 17.90
    "TH": 0.292,   # THB 129
    "VN": 0.208,   # VND 59000
    "PH": 0.250,   # PHP 194
    "ID": 0.292,   # IDR 54990
    "IN": 0.065,   # INR 119
    "PK": 0.125,   # PKR 349
    "BD": 0.100,   # BDT 149
    "LK": 0.125,   # LKR 499
    "AU": 0.958,   # AUD 13.99
    "NZ": 0.850,   # NZD 16.99
}

_REFERENCE_DATE = date(2024, 1, 1)


class SpotifyFetcher(IndexFetcher):
    """Spotify price index using hardcoded seed data.

    Spotify pricing data is maintained as a curated dataset since there
    is no stable public API for programmatic access.
    """

    index_type = "spotify"

    async def fetch(self) -> list[IndexRecord]:
        results: list[IndexRecord] = []
        for code, multiplier in _SPOTIFY_MULTIPLIERS.items():
            results.append(IndexRecord(
                territory_code=code,
                value=multiplier,
                reference_date=_REFERENCE_DATE,
            ))

        logger.info("Spotify fetcher: returned %d territory records", len(results))
        return results
