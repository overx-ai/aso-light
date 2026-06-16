from decimal import Decimal


def apply_vat(price: Decimal, vat_rate: Decimal | float) -> Decimal:
    """Apply VAT to a price.

    Args:
        price: Pre-VAT price.
        vat_rate: VAT rate (e.g. ``Decimal("0.20")`` for 20%). ``float`` is
            accepted for backward compatibility and coerced via ``str``.

    Returns:
        Price including VAT.
    """
    return price * (Decimal("1") + Decimal(str(vat_rate)))


def remove_vat(price_with_vat: Decimal, vat_rate: Decimal | float) -> Decimal:
    """Remove VAT from a price (get the pre-VAT price).

    Args:
        price_with_vat: Price that includes VAT.
        vat_rate: VAT rate (e.g. ``Decimal("0.20")`` for 20%). ``float`` is
            accepted for backward compatibility and coerced via ``str``.

    Returns:
        Price excluding VAT.
    """
    return price_with_vat / (Decimal("1") + Decimal(str(vat_rate)))
