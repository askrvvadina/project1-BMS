"""Pure loyalty point calculations shared by future application flows."""

from decimal import Decimal, ROUND_DOWN


EARNING_RATE = Decimal("0.01")
REDEMPTION_RATE = Decimal("0.20")


def _validated_amount(amount: Decimal, name: str) -> Decimal:
    if not isinstance(amount, Decimal):
        raise TypeError(f"{name} must be a Decimal.")
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be finite and nonnegative.")
    return amount


def calculate_earned_points(actual_paid_amount: Decimal) -> int:
    """Return 1% of the paid amount, rounded down to whole points."""
    amount = _validated_amount(actual_paid_amount, "actual_paid_amount")
    return int((amount * EARNING_RATE).to_integral_value(rounding=ROUND_DOWN))


def calculate_redemption_limit(
    purchase_amount: Decimal,
    available_points: int,
) -> int:
    """Return the lesser of available points and 20% of the purchase."""
    amount = _validated_amount(purchase_amount, "purchase_amount")
    if type(available_points) is not int or available_points < 0:
        raise ValueError("available_points must be a nonnegative integer.")
    purchase_limit = int(
        (amount * REDEMPTION_RATE).to_integral_value(rounding=ROUND_DOWN)
    )
    return min(available_points, purchase_limit)