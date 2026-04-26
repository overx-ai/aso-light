"""Exchange rate based price calculator."""

from app.services.pricing.proportional import ProportionalCalculator


class ExchangeRateCalculator(ProportionalCalculator):
    """Price calculator using live exchange rates.

    Reuses ProportionalCalculator's formula:
        adjusted_price = base_price * (exchange_rate / 1.0)

    Where index_value = the exchange rate (e.g., 159.09 for JPY/USD)
    and base_index_value = 1.0 (identity).
    """

    pass
