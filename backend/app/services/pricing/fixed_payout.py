from decimal import Decimal

from app.services.pricing.calculator import PriceCalculator


class FixedPayoutCalculator(PriceCalculator):
    """Calculate price to achieve a target payout after Apple's commission.

    Apple takes 30% (first year) or 15% (after first year / Small Business
    Program). The formula is:

        customer_price = target_payout / (1 - commission_rate)

    The index_value and base_index_value parameters are ignored since this
    calculator works purely from the target payout amount.
    """

    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.30"),
    ) -> None:
        self.commission_rate = commission_rate

    def calculate(
        self,
        base_price: Decimal,
        index_value: Decimal,
        base_index_value: Decimal,
    ) -> Decimal:
        # base_price IS the target payout; index values are not used.
        return base_price / (Decimal("1") - self.commission_rate)
