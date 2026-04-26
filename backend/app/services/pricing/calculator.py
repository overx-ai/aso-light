from abc import ABC, abstractmethod
from decimal import Decimal


class PriceCalculator(ABC):
    """Abstract base class for price calculation strategies."""

    @abstractmethod
    def calculate(
        self,
        base_price: Decimal,
        index_value: Decimal,
        base_index_value: Decimal,
    ) -> Decimal:
        """Calculate adjusted price for a territory.

        Args:
            base_price: The reference price (e.g., US price).
            index_value: The economic index value for the target territory.
            base_index_value: The economic index value for the base territory
                (usually US).

        Returns:
            The adjusted price for the target territory.
        """
        ...
