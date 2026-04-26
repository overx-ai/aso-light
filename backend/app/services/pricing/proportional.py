from decimal import Decimal

from app.services.pricing.calculator import PriceCalculator


class ProportionalCalculator(PriceCalculator):
    """Calculate price using proportional adjustment based on index ratios.

    Formula: adjusted_price = base_price * (index_value / base_index_value)

    Used by PPP, Big Mac, Netflix, and Spotify calculators since they all
    apply the same proportional scaling logic.
    """

    def calculate(
        self,
        base_price: Decimal,
        index_value: Decimal,
        base_index_value: Decimal,
    ) -> Decimal:
        if base_index_value == 0:
            return base_price
        return base_price * (index_value / base_index_value)
