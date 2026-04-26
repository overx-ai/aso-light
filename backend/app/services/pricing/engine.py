from decimal import Decimal

from app.services.pricing.bigmac import BigMacCalculator
from app.services.pricing.calculator import PriceCalculator
from app.services.pricing.charming import apply_charming
from app.services.pricing.exchange_rate import ExchangeRateCalculator
from app.services.pricing.fixed_payout import FixedPayoutCalculator
from app.services.pricing.netflix import NetflixCalculator
from app.services.pricing.ppp import PPPCalculator
from app.services.pricing.spotify import SpotifyCalculator
from app.services.pricing.vat import apply_vat

CALCULATORS: dict[str, type[PriceCalculator]] = {
    "ppp": PPPCalculator,
    "bigmac": BigMacCalculator,
    "netflix": NetflixCalculator,
    "spotify": SpotifyCalculator,
    "fixed_payout": FixedPayoutCalculator,
    "exchange_rate": ExchangeRateCalculator,
}


class PriceEngine:
    """Orchestrates price calculation: index lookup -> calculate -> VAT -> charming."""

    def __init__(
        self,
        index_type: str,
        base_price: Decimal,
        base_territory_code: str = "US",
        apply_vat_flag: bool = False,
        charming_mode: str = "none",
    ) -> None:
        calculator_cls = CALCULATORS.get(index_type)
        if calculator_cls is None:
            raise ValueError(f"Unknown index type: {index_type}")
        self.calculator = calculator_cls()
        self.base_price = base_price
        self.base_territory_code = base_territory_code
        self.apply_vat_flag = apply_vat_flag
        self.charming_mode = charming_mode

    def calculate_price(
        self,
        index_value: float,
        base_index_value: float,
        vat_rate: float = 0.0,
    ) -> Decimal:
        """Calculate final price for a territory.

        Args:
            index_value: Economic index value for the target territory.
            base_index_value: Economic index value for the base territory.
            vat_rate: VAT rate as decimal (e.g. 0.20 for 20%).

        Returns:
            The final adjusted, optionally VAT-inclusive, charming-rounded price.
        """
        adjusted = self.calculator.calculate(
            self.base_price,
            Decimal(str(index_value)),
            Decimal(str(base_index_value)),
        )

        if self.apply_vat_flag and vat_rate > 0:
            adjusted = apply_vat(adjusted, vat_rate)

        adjusted = apply_charming(adjusted, self.charming_mode)

        return adjusted
