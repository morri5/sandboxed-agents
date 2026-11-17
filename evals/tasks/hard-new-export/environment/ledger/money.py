"""Money arithmetic: `Decimal` only, rounded half up to the cent."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


class MoneyError(ValueError):
    pass


def to_decimal(value: object) -> Decimal:
    """A `Decimal` from a string, an int or a `Decimal`. Floats are refused: they are not exact."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MoneyError(f"not an exact number: {value!r}")
    try:
        return Decimal(value.strip() if isinstance(value, str) else value)
    except InvalidOperation:
        raise MoneyError(f"not a number: {value!r}") from None


def round_cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_amount(amount: Decimal, currency: str) -> str:
    return f"{round_cents(amount):,.2f} {currency}"
