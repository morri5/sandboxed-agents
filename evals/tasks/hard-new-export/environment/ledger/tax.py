"""Tax rates by code."""

from decimal import Decimal

RATES = {
    "standard": Decimal("0.19"),
    "reduced": Decimal("0.07"),
    "zero": Decimal("0"),
}


class UnknownTaxCode(KeyError):
    pass


def rate(code: str) -> Decimal:
    try:
        return RATES[code]
    except KeyError:
        raise UnknownTaxCode(code) from None
