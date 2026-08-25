"""Classic iTunes storefront IDs, keyed by ISO 3166-1 alpha-2 country code.

These are the numeric ids used in the ``X-Apple-Store-Front: {id}-1,29`` request
header that iTunes/App Store web services (MZSearchHints, MZStore, …) use to pick
a storefront. Without that header Apple answers with a well-formed but **empty**
payload — HTTP 200, no error — which is how ``keywords_suggestions`` silently
returned ``[]`` for its entire life. See spec 012 R2.

**This is NOT ``Territory.apple_territory_id``.** That column holds an App Store
Connect identifier (the ASC API's own territory resource id, alpha-3 keyed); the
two numbering schemes are unrelated and must never be substituted for one another.
This module is also unrelated to the ``l=`` locale query parameter, which the
hints endpoint ignores — the header alone selects the storefront *and* its
language.

Coverage: every country in :mod:`app.data.territories` resolves, either directly
or through :data:`STOREFRONT_ALIASES`, except the codes in
:data:`TERRITORIES_WITHOUT_STOREFRONT` for which Apple has never published a
classic storefront id. Those fall back to ``us`` with a warning.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_COUNTRY = "us"

#: ISO alpha-2 (lowercase) → classic iTunes storefront id.
STOREFRONTS: dict[str, int] = {
    "ae": 143481,  # United Arab Emirates
    "af": 143610,  # Afghanistan
    "ag": 143540,  # Antigua and Barbuda
    "ai": 143538,  # Anguilla
    "al": 143575,  # Albania
    "am": 143524,  # Armenia
    "ao": 143564,  # Angola
    "ar": 143505,  # Argentina
    "at": 143445,  # Austria
    "au": 143460,  # Australia
    "az": 143568,  # Azerbaijan
    "ba": 143612,  # Bosnia and Herzegovina
    "bb": 143541,  # Barbados
    "bd": 143490,  # Bangladesh
    "be": 143446,  # Belgium
    "bf": 143578,  # Burkina Faso
    "bg": 143526,  # Bulgaria
    "bh": 143559,  # Bahrain
    "bj": 143576,  # Benin
    "bm": 143542,  # Bermuda
    "bn": 143560,  # Brunei
    "bo": 143556,  # Bolivia
    "br": 143503,  # Brazil
    "bs": 143539,  # Bahamas
    "bt": 143577,  # Bhutan
    "bw": 143525,  # Botswana
    "by": 143565,  # Belarus
    "bz": 143555,  # Belize
    "ca": 143455,  # Canada
    "cd": 143613,  # Democratic Republic of Congo
    "cg": 143582,  # Republic of Congo
    "ch": 143459,  # Switzerland
    "ci": 143527,  # Cote d'Ivoire
    "cl": 143483,  # Chile
    "cm": 143574,  # Cameroon
    "cn": 143465,  # China mainland
    "co": 143501,  # Colombia
    "cr": 143495,  # Costa Rica
    "cv": 143580,  # Cape Verde
    "cy": 143557,  # Cyprus
    "cz": 143489,  # Czech Republic
    "de": 143443,  # Germany
    "dk": 143458,  # Denmark
    "dm": 143545,  # Dominica
    "do": 143508,  # Dominican Republic
    "dz": 143563,  # Algeria
    "ec": 143509,  # Ecuador
    "ee": 143518,  # Estonia
    "eg": 143516,  # Egypt
    "es": 143454,  # Spain
    "fi": 143447,  # Finland
    "fj": 143583,  # Fiji
    "fm": 143591,  # Micronesia
    "fr": 143442,  # France
    "ga": 143614,  # Gabon
    "gb": 143444,  # United Kingdom
    "gd": 143546,  # Grenada
    "ge": 143615,  # Georgia
    "gh": 143573,  # Ghana
    "gm": 143584,  # Gambia
    "gr": 143448,  # Greece
    "gt": 143504,  # Guatemala
    "gw": 143585,  # Guinea-Bissau
    "gy": 143553,  # Guyana
    "hk": 143463,  # Hong Kong
    "hn": 143510,  # Honduras
    "hr": 143494,  # Croatia
    "hu": 143482,  # Hungary
    "id": 143476,  # Indonesia
    "ie": 143449,  # Ireland
    "il": 143491,  # Israel
    "in": 143467,  # India
    "iq": 143617,  # Iraq
    "is": 143558,  # Iceland
    "it": 143450,  # Italy
    "jm": 143511,  # Jamaica
    "jo": 143528,  # Jordan
    "jp": 143462,  # Japan
    "ke": 143529,  # Kenya
    "kg": 143586,  # Kyrgyzstan
    "kh": 143579,  # Cambodia
    "kn": 143548,  # St. Kitts and Nevis
    "kr": 143466,  # South Korea
    "kw": 143493,  # Kuwait
    "ky": 143544,  # Cayman Islands
    "kz": 143517,  # Kazakhstan
    "la": 143587,  # Laos
    "lb": 143497,  # Lebanon
    "lc": 143549,  # St. Lucia
    "li": 143522,  # Liechtenstein (storefront exists; not an ASC territory row)
    "lk": 143486,  # Sri Lanka
    "lr": 143588,  # Liberia
    "lt": 143520,  # Lithuania
    "lu": 143451,  # Luxembourg
    "lv": 143519,  # Latvia
    "ly": 143567,  # Libya
    "ma": 143620,  # Morocco
    "md": 143523,  # Moldova
    "me": 143619,  # Montenegro
    "mg": 143531,  # Madagascar
    "mk": 143530,  # North Macedonia
    "ml": 143532,  # Mali
    "mm": 143570,  # Myanmar
    "mn": 143592,  # Mongolia
    "mo": 143515,  # Macau
    "mr": 143590,  # Mauritania
    "ms": 143547,  # Montserrat
    "mt": 143521,  # Malta
    "mu": 143533,  # Mauritius
    "mv": 143488,  # Maldives
    "mw": 143589,  # Malawi
    "mx": 143468,  # Mexico
    "my": 143473,  # Malaysia
    "mz": 143593,  # Mozambique
    "na": 143594,  # Namibia
    "ne": 143534,  # Niger
    "ng": 143561,  # Nigeria
    "ni": 143512,  # Nicaragua
    "nl": 143452,  # Netherlands
    "no": 143457,  # Norway
    "np": 143484,  # Nepal
    "nr": 143606,  # Nauru
    "nz": 143461,  # New Zealand
    "om": 143562,  # Oman
    "pa": 143485,  # Panama
    "pe": 143507,  # Peru
    "pg": 143597,  # Papua New Guinea
    "ph": 143474,  # Philippines
    "pk": 143477,  # Pakistan
    "pl": 143478,  # Poland
    "pt": 143453,  # Portugal
    "pw": 143595,  # Palau
    "py": 143513,  # Paraguay
    "qa": 143498,  # Qatar
    "ro": 143487,  # Romania
    "rs": 143500,  # Serbia
    "ru": 143469,  # Russia
    "rw": 143621,  # Rwanda
    "sa": 143479,  # Saudi Arabia
    "sb": 143601,  # Solomon Islands
    "sc": 143599,  # Seychelles
    "se": 143456,  # Sweden
    "sg": 143464,  # Singapore
    "si": 143499,  # Slovenia
    "sk": 143496,  # Slovakia
    "sl": 143600,  # Sierra Leone
    "sn": 143535,  # Senegal
    "sr": 143554,  # Suriname
    "st": 143598,  # Sao Tome and Principe
    "sv": 143506,  # El Salvador
    "sz": 143602,  # Eswatini
    "tc": 143552,  # Turks and Caicos
    "td": 143581,  # Chad
    "th": 143475,  # Thailand
    "tj": 143603,  # Tajikistan
    "tm": 143604,  # Turkmenistan
    "tn": 143536,  # Tunisia
    "to": 143608,  # Tonga
    "tr": 143480,  # Turkey
    "tt": 143551,  # Trinidad and Tobago
    "tw": 143470,  # Taiwan
    "tz": 143572,  # Tanzania
    "ua": 143492,  # Ukraine
    "ug": 143537,  # Uganda
    "us": 143441,  # United States
    "uy": 143514,  # Uruguay
    "uz": 143566,  # Uzbekistan
    "vc": 143550,  # St. Vincent and the Grenadines
    "ve": 143502,  # Venezuela
    "vg": 143543,  # British Virgin Islands
    "vn": 143471,  # Vietnam
    "vu": 143609,  # Vanuatu
    "xk": 143624,  # Kosovo
    "ye": 143571,  # Yemen
    "za": 143472,  # South Africa
    "zm": 143622,  # Zambia
    "zw": 143605,  # Zimbabwe
}

#: ASC territories Apple serves from another country's storefront. They are real
#: ``territories.py`` rows (they get their own price schedule) but they have no
#: storefront id of their own, so hints for them come from the parent store.
STOREFRONT_ALIASES: dict[str, str] = {
    "mc": "fr",  # Monaco — served by the French store
    "pr": "us",  # Puerto Rico — part of the US store
    "gf": "fr",  # French Guiana
    "gp": "fr",  # Guadeloupe
    "mq": "fr",  # Martinique
    "re": "fr",  # Reunion
    "yt": "fr",  # Mayotte
    "nc": "fr",  # New Caledonia
    "pf": "fr",  # French Polynesia
}

#: ``territories.py`` codes with no known classic storefront id and no defensible
#: parent store. Kept explicit so the ``us`` fallback below is a documented
#: decision rather than a silent one — do not invent ids for these.
TERRITORIES_WITHOUT_STOREFRONT: frozenset[str] = frozenset(
    {
        "bi",  # Burundi
        "cf",  # Central African Republic
        "cu",  # Cuba
        "dj",  # Djibouti
        "er",  # Eritrea
        "et",  # Ethiopia
        "gn",  # Guinea
        "gq",  # Equatorial Guinea
        "ht",  # Haiti
        "km",  # Comoros
        "ls",  # Lesotho
        "mh",  # Marshall Islands
        "ps",  # Palestine
        "sd",  # Sudan
        "so",  # Somalia
        "ss",  # South Sudan
        "tg",  # Togo
        "ws",  # Samoa
    }
)


def normalize_country(value: str | None) -> str:
    """Coerce a country/locale-ish string to a lowercase alpha-2 country code.

    Accepts ``"US"``, ``"us"``, ``"en_us"``, ``"pt-BR"`` — the locale forms exist
    because ``get_suggestions`` used to take an iTunes ``locale`` and callers may
    still pass one. Returns :data:`DEFAULT_COUNTRY` for empty input.
    """
    if not value:
        return DEFAULT_COUNTRY
    code = value.strip().lower().replace("-", "_")
    if "_" in code:
        code = code.rsplit("_", 1)[-1]
    return code or DEFAULT_COUNTRY


def resolve_storefront(country: str | None) -> tuple[int, str]:
    """Return ``(storefront_id, resolved_country)`` for ``country``.

    Unknown or unmapped countries fall back to ``us`` and log a warning — an
    unnoticed fallback is exactly how the header bug hid for so long.
    """
    code = normalize_country(country)
    storefront = STOREFRONTS.get(code)
    if storefront is not None:
        return storefront, code

    alias = STOREFRONT_ALIASES.get(code)
    if alias is not None:
        return STOREFRONTS[alias], alias

    logger.warning(
        "No iTunes storefront for country=%r (%s); falling back to %r",
        code,
        "no storefront published by Apple"
        if code in TERRITORIES_WITHOUT_STOREFRONT
        else "unknown country code",
        DEFAULT_COUNTRY,
    )
    return STOREFRONTS[DEFAULT_COUNTRY], DEFAULT_COUNTRY


def storefront_header(country: str | None) -> tuple[str, str]:
    """Return ``(header_value, resolved_country)`` for ``X-Apple-Store-Front``.

    The ``-1,29`` suffix is the platform/client tuple iTunes clients send; Apple
    returns an empty payload when it is missing.
    """
    storefront, code = resolve_storefront(country)
    return f"{storefront}-1,29", code
