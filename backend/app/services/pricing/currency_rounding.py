"""Smart currency-aware price rounding.

Rounds prices to 'nice' values appropriate for each currency,
with +-10% flexibility to find the best candidate.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class CurrencyProfile:
    """Defines rounding behavior for a currency."""

    decimals: int  # 0, 2, or 3
    # List of (threshold, step, suffix) tuples sorted by threshold ascending.
    # For a price >= threshold, round to nearest `step` then add `suffix`.
    # Example for JPY: [(0, 10, -10), (10000, 100, -100)]
    #   means <10000: round to 10s, end in 0 (e.g., 990, 1490)
    #          >=10000: round to 100s, end in 00 (e.g., 14900)
    tiers: list[tuple[int, int, int]] = field(default_factory=list)
    # For 2-decimal currencies: the decimal suffix (e.g., 0.99, 0.90, 0.00)
    decimal_suffix: str = "0.99"
    min_price: str = "0.99"  # Minimum allowed price


# --- Currency Profile Definitions ---

# Standard 2-decimal currencies ending in .99
_STANDARD_99 = CurrencyProfile(
    decimals=2,
    decimal_suffix="0.99",
    min_price="0.99",
)

# BRL: ending in .90
_BRL = CurrencyProfile(
    decimals=2,
    decimal_suffix="0.90",
    min_price="0.90",
)

# RUB: ending in .00 (whole numbers)
_RUB = CurrencyProfile(
    decimals=2,
    decimal_suffix="0.00",
    min_price="1.00",
)

# 3-decimal (minor unit 1000) currencies: KWD, BHD, OMR, JOD, TND.
# ISO-4217 mandates three fractional digits. Apple's tier ladder for
# these uses a trailing .x99 (e.g. 0.499, 0.999, 1.999) — mirror the
# .99-style charm one place further right.
_STANDARD_3DP = CurrencyProfile(
    decimals=3,
    decimal_suffix="0.999",
    min_price="0.999",
)

# JPY, TWD: round to 10s, trailing 90 (990, 1490, 2990)
_JPY = CurrencyProfile(
    decimals=0,
    tiers=[(0, 10, -10), (10000, 100, -100)],
    min_price="100",
)

# KRW, CLP, COP: round to 100s, trailing 900 (9900, 14900)
_KRW = CurrencyProfile(
    decimals=0,
    tiers=[(0, 100, -100), (100000, 1000, -1000)],
    min_price="900",
)

# VND, IDR: round to 1000s (249000, 499000)
_VND = CurrencyProfile(
    decimals=0,
    tiers=[(0, 1000, 0), (1000000, 10000, 0)],
    min_price="10000",
)

# INR, PKR: whole numbers ending in 9 (99, 199, 299, 799, 999, 1499, 4999)
_INR = CurrencyProfile(
    decimals=0,
    tiers=[(0, 100, -1), (1000, 500, -1), (10000, 1000, -1)],
    min_price="49",
)

# HUF, ISK: round to 10s
_HUF = CurrencyProfile(
    decimals=0,
    tiers=[(0, 10, -10), (10000, 100, -100)],
    min_price="100",
)

# PHP, THB: round to 10s ending in 0 or 9
_PHP = CurrencyProfile(
    decimals=0,
    tiers=[(0, 10, -1), (10000, 100, -1)],
    min_price="49",
)

# ARS, NGN and similar high-value 2-decimal currencies:
# x9.99, x99.99 at higher values (e.g., 99.99, 199.99, 1099.99, 4099.99)
_ARS = CurrencyProfile(
    decimals=2,
    decimal_suffix="0.99",
    min_price="0.99",
    tiers=[(0, 10, 0), (100, 50, 0), (500, 100, 0), (5000, 500, 0)],
)

# TWD: same as JPY
_TWD = _JPY


CURRENCY_PROFILES: dict[str, CurrencyProfile] = {
    # Standard .99 currencies (2 decimals)
    "USD": _STANDARD_99,
    "EUR": _STANDARD_99,
    "GBP": _STANDARD_99,
    "AUD": _STANDARD_99,
    "CAD": _STANDARD_99,
    "CHF": _STANDARD_99,
    "NZD": _STANDARD_99,
    "SGD": _STANDARD_99,
    "HKD": _STANDARD_99,
    "NOK": _STANDARD_99,
    "SEK": _STANDARD_99,
    "DKK": _STANDARD_99,
    "PLN": _STANDARD_99,
    "CZK": _STANDARD_99,
    "TRY": _STANDARD_99,
    "ZAR": _STANDARD_99,
    "MXN": _STANDARD_99,
    "MYR": _STANDARD_99,
    "AED": _STANDARD_99,
    "SAR": _STANDARD_99,
    "QAR": _STANDARD_99,
    "RON": _STANDARD_99,
    "BGN": _STANDARD_99,
    "HRK": _STANDARD_99,
    "PEN": _STANDARD_99,
    "EGP": _STANDARD_99,
    "NGN": _STANDARD_99,
    "KES": _STANDARD_99,
    "GHS": _STANDARD_99,
    "TZS": _STANDARD_99,
    "ILS": _STANDARD_99,
    # 3-decimal (minor unit 1000) currencies
    "KWD": _STANDARD_3DP,
    "BHD": _STANDARD_3DP,
    "OMR": _STANDARD_3DP,
    "JOD": _STANDARD_3DP,
    "TND": _STANDARD_3DP,
    # ARS
    "ARS": _ARS,
    # BRL
    "BRL": _BRL,
    # RUB
    "RUB": _RUB,
    # JPY
    "JPY": _JPY,
    # TWD
    "TWD": _TWD,
    # KRW
    "KRW": _KRW,
    # CLP
    "CLP": _KRW,
    # COP
    "COP": _KRW,
    # VND
    "VND": _VND,
    # IDR
    "IDR": _VND,
    # INR, PKR
    "INR": _INR,
    "PKR": _INR,
    "BDT": _INR,
    "LKR": _INR,
    # HUF
    "HUF": _HUF,
    # ISK
    "ISK": _HUF,
    # PHP
    "PHP": _PHP,
    # THB
    "THB": _PHP,
}

DEFAULT_PROFILE = _STANDARD_99

# Quantization step per decimal-place count, used by the fallback path.
_QUANTUM_BY_DECIMALS: dict[int, Decimal] = {
    0: Decimal("1"),
    2: Decimal("0.01"),
    3: Decimal("0.001"),
}


def _get_tier(price: Decimal, profile: CurrencyProfile) -> tuple[int, int]:
    """Get the (step, suffix) for the price's magnitude tier."""
    step, suffix = profile.tiers[0][1], profile.tiers[0][2]
    for threshold, tier_step, tier_suffix in profile.tiers:
        if int(price) >= threshold:
            step, suffix = tier_step, tier_suffix
    return step, suffix


def _round_to_step(price: Decimal, step: int) -> int:
    """Round ``price`` to the nearest multiple of ``step`` (ROUND_HALF_UP).

    Uses Decimal arithmetic so large 0-decimal prices (KRW/VND millions)
    don't lose a step to float rounding error.
    """
    if step <= 0:
        return int(price)
    step_dec = Decimal(step)
    multiples = (price / step_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(multiples * step_dec)


def _generate_nice_candidates(
    price: Decimal,
    profile: CurrencyProfile,
) -> list[Decimal]:
    """Generate candidate 'nice' prices around the raw price."""
    candidates: list[Decimal] = []

    if profile.decimals in (2, 3):
        suffix = Decimal(profile.decimal_suffix)
        if profile.tiers:
            # Tiered fractional: use step sizes for larger currencies (ARS, etc.)
            step, _ = _get_tier(price, profile)
            base_rounded = _round_to_step(price, step)
            for offset_mult in range(-5, 6):
                candidate_int = base_rounded + offset_mult * step
                if candidate_int < 0:
                    continue
                # e.g. 4100 - 1 + 0.99 = 4099.99
                candidates.append(Decimal(candidate_int) - Decimal("1") + suffix)
        else:
            base_int = int(price)
            for offset in range(-3, 4):
                candidate_int = base_int + offset
                if candidate_int < 0:
                    continue
                candidates.append(Decimal(candidate_int) + suffix)
    else:
        # Zero-decimal currencies
        step, suffix_offset = _get_tier(price, profile)
        base_rounded = _round_to_step(price, step)
        for offset_mult in range(-5, 6):
            candidate = base_rounded + offset_mult * step
            if candidate <= 0:
                continue
            # Primary: apply suffix offset (e.g., 800 + (-1) = 799 for INR)
            charmed = candidate + suffix_offset
            if charmed > 0:
                candidates.append(Decimal(charmed))

    # Deduplicate
    return list(dict.fromkeys(candidates))


def apply_currency_rounding(
    price: Decimal,
    currency_code: str,
    flex_pct: Decimal = Decimal("0.10"),
) -> Decimal:
    """Round price to the nicest value within +-flex_pct of the raw price.

    Args:
        price: Raw converted price in local currency.
        currency_code: ISO 4217 currency code (e.g., "JPY", "EUR").
        flex_pct: Maximum allowed deviation from raw price (default 10%).

    Returns:
        A 'nice' rounded price appropriate for the currency.
    """
    if price <= 0:
        return price

    profile = CURRENCY_PROFILES.get(currency_code, DEFAULT_PROFILE)
    min_price = Decimal(profile.min_price)

    candidates = _generate_nice_candidates(price, profile)

    # Filter to candidates within +-flex_pct of raw price
    lower = price * (Decimal("1") - flex_pct)
    upper = price * (Decimal("1") + flex_pct)
    valid = [c for c in candidates if lower <= c <= upper and c >= min_price]

    if not valid:
        # Fallback: just use the closest candidate above min_price
        above_min = [c for c in candidates if c >= min_price]
        if above_min:
            return min(above_min, key=lambda c: abs(c - price))
        return max(
            price.quantize(_QUANTUM_BY_DECIMALS[profile.decimals]),
            min_price,
        )

    # Pick the closest valid candidate to the raw price
    return min(valid, key=lambda c: abs(c - price))
