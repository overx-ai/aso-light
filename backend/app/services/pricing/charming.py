from decimal import Decimal


def apply_charming(price: Decimal, mode: str) -> Decimal:
    """Round price to a 'charming' value (.99 or .95).

    Args:
        price: The calculated price.
        mode: "none" (no rounding), "99" (round to .99), "95" (round to .95).

    Returns:
        The charming price, always quantized to two decimal places.
    """
    if mode == "none":
        return price.quantize(Decimal("0.01"))

    integer_part = int(price)
    fractional = price - integer_part

    if mode == "99":
        suffix = Decimal("0.99")
    elif mode == "95":
        suffix = Decimal("0.95")
    else:
        return price.quantize(Decimal("0.01"))

    # Decide whether to round down to (integer_part - 1).XX or stay at
    # integer_part.XX. If the fractional part is below 0.50, round down
    # to the previous integer; otherwise keep the current one.
    if fractional >= suffix:
        return Decimal(f"{integer_part}") + suffix
    elif fractional < Decimal("0.50"):
        base = max(integer_part - 1, 0)
        return Decimal(f"{base}") + suffix
    else:
        return Decimal(f"{integer_part}") + suffix
