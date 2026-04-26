"""Static cross-localization data for App Store territories.

Defines which locales are indexed in which App Store territories.
Source: Apple documentation and ASO community knowledge.
"""

from __future__ import annotations

CROSS_LOCALIZATION_DATA: list[dict] = [
    # US App Store
    {"territory_code": "US", "locale": "en-US", "is_indexed": True},
    {"territory_code": "US", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "US", "locale": "fr-FR", "is_indexed": True},
    {"territory_code": "US", "locale": "pt-BR", "is_indexed": True},
    {"territory_code": "US", "locale": "zh-Hans", "is_indexed": True},
    {"territory_code": "US", "locale": "zh-Hant", "is_indexed": True},
    {"territory_code": "US", "locale": "ko", "is_indexed": True},
    {"territory_code": "US", "locale": "vi", "is_indexed": True},
    {"territory_code": "US", "locale": "ar", "is_indexed": True},
    {"territory_code": "US", "locale": "ru", "is_indexed": True},
    # UK
    {"territory_code": "GB", "locale": "en-GB", "is_indexed": True},
    {"territory_code": "GB", "locale": "en-US", "is_indexed": True},
    # Germany
    {"territory_code": "DE", "locale": "de-DE", "is_indexed": True},
    {"territory_code": "DE", "locale": "en-US", "is_indexed": True},
    # France
    {"territory_code": "FR", "locale": "fr-FR", "is_indexed": True},
    {"territory_code": "FR", "locale": "en-US", "is_indexed": True},
    # Japan
    {"territory_code": "JP", "locale": "ja", "is_indexed": True},
    {"territory_code": "JP", "locale": "en-US", "is_indexed": True},
    # China
    {"territory_code": "CN", "locale": "zh-Hans", "is_indexed": True},
    {"territory_code": "CN", "locale": "en-US", "is_indexed": True},
    # South Korea
    {"territory_code": "KR", "locale": "ko", "is_indexed": True},
    {"territory_code": "KR", "locale": "en-US", "is_indexed": True},
    # Russia
    {"territory_code": "RU", "locale": "ru", "is_indexed": True},
    {"territory_code": "RU", "locale": "en-US", "is_indexed": True},
    # Brazil
    {"territory_code": "BR", "locale": "pt-BR", "is_indexed": True},
    {"territory_code": "BR", "locale": "en-US", "is_indexed": True},
    {"territory_code": "BR", "locale": "es-MX", "is_indexed": True},
    # Mexico
    {"territory_code": "MX", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "MX", "locale": "en-US", "is_indexed": True},
    # Italy
    {"territory_code": "IT", "locale": "it", "is_indexed": True},
    {"territory_code": "IT", "locale": "en-US", "is_indexed": True},
    # Spain
    {"territory_code": "ES", "locale": "es-ES", "is_indexed": True},
    {"territory_code": "ES", "locale": "en-US", "is_indexed": True},
    # Australia
    {"territory_code": "AU", "locale": "en-AU", "is_indexed": True},
    {"territory_code": "AU", "locale": "en-US", "is_indexed": True},
    # Canada
    {"territory_code": "CA", "locale": "en-CA", "is_indexed": True},
    {"territory_code": "CA", "locale": "fr-CA", "is_indexed": True},
    {"territory_code": "CA", "locale": "en-US", "is_indexed": True},
    # India
    {"territory_code": "IN", "locale": "en-US", "is_indexed": True},
    {"territory_code": "IN", "locale": "hi", "is_indexed": True},
    # Turkey
    {"territory_code": "TR", "locale": "tr", "is_indexed": True},
    {"territory_code": "TR", "locale": "en-US", "is_indexed": True},
    # Netherlands
    {"territory_code": "NL", "locale": "nl", "is_indexed": True},
    {"territory_code": "NL", "locale": "en-US", "is_indexed": True},
    # Sweden
    {"territory_code": "SE", "locale": "sv", "is_indexed": True},
    {"territory_code": "SE", "locale": "en-US", "is_indexed": True},
    # Poland
    {"territory_code": "PL", "locale": "pl", "is_indexed": True},
    {"territory_code": "PL", "locale": "en-US", "is_indexed": True},
    # Taiwan
    {"territory_code": "TW", "locale": "zh-Hant", "is_indexed": True},
    {"territory_code": "TW", "locale": "en-US", "is_indexed": True},
    # Saudi Arabia
    {"territory_code": "SA", "locale": "ar", "is_indexed": True},
    {"territory_code": "SA", "locale": "en-US", "is_indexed": True},
    # UAE
    {"territory_code": "AE", "locale": "ar", "is_indexed": True},
    {"territory_code": "AE", "locale": "en-US", "is_indexed": True},
    # Thailand
    {"territory_code": "TH", "locale": "th", "is_indexed": True},
    {"territory_code": "TH", "locale": "en-US", "is_indexed": True},
    # Vietnam
    {"territory_code": "VN", "locale": "vi", "is_indexed": True},
    {"territory_code": "VN", "locale": "en-US", "is_indexed": True},
    # Indonesia
    {"territory_code": "ID", "locale": "id", "is_indexed": True},
    {"territory_code": "ID", "locale": "en-US", "is_indexed": True},
    # Portugal
    {"territory_code": "PT", "locale": "pt-PT", "is_indexed": True},
    {"territory_code": "PT", "locale": "en-US", "is_indexed": True},
    # Norway
    {"territory_code": "NO", "locale": "nb", "is_indexed": True},
    {"territory_code": "NO", "locale": "en-US", "is_indexed": True},
    # Denmark
    {"territory_code": "DK", "locale": "da", "is_indexed": True},
    {"territory_code": "DK", "locale": "en-US", "is_indexed": True},
    # Finland
    {"territory_code": "FI", "locale": "fi", "is_indexed": True},
    {"territory_code": "FI", "locale": "en-US", "is_indexed": True},
    # Austria
    {"territory_code": "AT", "locale": "de-DE", "is_indexed": True},
    {"territory_code": "AT", "locale": "en-US", "is_indexed": True},
    # Switzerland
    {"territory_code": "CH", "locale": "de-DE", "is_indexed": True},
    {"territory_code": "CH", "locale": "fr-FR", "is_indexed": True},
    {"territory_code": "CH", "locale": "it", "is_indexed": True},
    {"territory_code": "CH", "locale": "en-US", "is_indexed": True},
    # Belgium
    {"territory_code": "BE", "locale": "nl", "is_indexed": True},
    {"territory_code": "BE", "locale": "fr-FR", "is_indexed": True},
    {"territory_code": "BE", "locale": "en-US", "is_indexed": True},
    # Israel
    {"territory_code": "IL", "locale": "he", "is_indexed": True},
    {"territory_code": "IL", "locale": "en-US", "is_indexed": True},
    # Singapore
    {"territory_code": "SG", "locale": "en-US", "is_indexed": True},
    {"territory_code": "SG", "locale": "zh-Hans", "is_indexed": True},
    # Hong Kong
    {"territory_code": "HK", "locale": "zh-Hant", "is_indexed": True},
    {"territory_code": "HK", "locale": "en-US", "is_indexed": True},
    # Malaysia
    {"territory_code": "MY", "locale": "ms", "is_indexed": True},
    {"territory_code": "MY", "locale": "en-US", "is_indexed": True},
    {"territory_code": "MY", "locale": "zh-Hans", "is_indexed": True},
    # Philippines
    {"territory_code": "PH", "locale": "en-US", "is_indexed": True},
    # Colombia
    {"territory_code": "CO", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "CO", "locale": "en-US", "is_indexed": True},
    # Argentina
    {"territory_code": "AR", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "AR", "locale": "en-US", "is_indexed": True},
    # Chile
    {"territory_code": "CL", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "CL", "locale": "en-US", "is_indexed": True},
    # Peru
    {"territory_code": "PE", "locale": "es-MX", "is_indexed": True},
    {"territory_code": "PE", "locale": "en-US", "is_indexed": True},
    # Egypt
    {"territory_code": "EG", "locale": "ar", "is_indexed": True},
    {"territory_code": "EG", "locale": "en-US", "is_indexed": True},
    # South Africa
    {"territory_code": "ZA", "locale": "en-US", "is_indexed": True},
    # Nigeria
    {"territory_code": "NG", "locale": "en-US", "is_indexed": True},
    # New Zealand
    {"territory_code": "NZ", "locale": "en-US", "is_indexed": True},
    {"territory_code": "NZ", "locale": "en-AU", "is_indexed": True},
    # Ireland
    {"territory_code": "IE", "locale": "en-GB", "is_indexed": True},
    {"territory_code": "IE", "locale": "en-US", "is_indexed": True},
    # Czech Republic
    {"territory_code": "CZ", "locale": "cs", "is_indexed": True},
    {"territory_code": "CZ", "locale": "en-US", "is_indexed": True},
    # Romania
    {"territory_code": "RO", "locale": "ro", "is_indexed": True},
    {"territory_code": "RO", "locale": "en-US", "is_indexed": True},
    # Hungary
    {"territory_code": "HU", "locale": "hu", "is_indexed": True},
    {"territory_code": "HU", "locale": "en-US", "is_indexed": True},
    # Greece
    {"territory_code": "GR", "locale": "el", "is_indexed": True},
    {"territory_code": "GR", "locale": "en-US", "is_indexed": True},
    # Ukraine
    {"territory_code": "UA", "locale": "uk", "is_indexed": True},
    {"territory_code": "UA", "locale": "en-US", "is_indexed": True},
    {"territory_code": "UA", "locale": "ru", "is_indexed": True},
]

# Pre-computed lookup: territory_code -> list of indexed locales
_LOCALES_BY_TERRITORY: dict[str, list[str]] = {}
# Pre-computed lookup: locale -> list of territory codes
_TERRITORIES_BY_LOCALE: dict[str, list[str]] = {}

for _entry in CROSS_LOCALIZATION_DATA:
    if _entry["is_indexed"]:
        _tc = _entry["territory_code"]
        _loc = _entry["locale"]
        _LOCALES_BY_TERRITORY.setdefault(_tc, []).append(_loc)
        _TERRITORIES_BY_LOCALE.setdefault(_loc, []).append(_tc)


def get_cross_localization_table() -> list[dict]:
    """Return the full cross-localization data."""
    return CROSS_LOCALIZATION_DATA


def get_indexed_locales_for_territory(territory_code: str) -> list[str]:
    """Get all locales that are indexed in a given territory."""
    return _LOCALES_BY_TERRITORY.get(territory_code, [])


def get_territories_for_locale(locale: str) -> list[str]:
    """Get all territories where a given locale is indexed."""
    return _TERRITORIES_BY_LOCALE.get(locale, [])
