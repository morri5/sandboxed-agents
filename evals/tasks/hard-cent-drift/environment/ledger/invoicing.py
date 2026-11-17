"""Line and invoice amounts.

Each line is rounded on its own: the net (quantity times unit price) half up to
the cent, then the tax on that net half up to the cent. An invoice's net, tax and
total are sums of its rounded lines, so they always add up on paper.
"""

from decimal import Decimal

from ledger import tax
from ledger.models import Invoice, Line
from ledger.money import round_cents

ZERO = Decimal("0.00")


def line_net(line: Line) -> Decimal:
    return round_cents(line.quantity * line.unit_price)


def line_tax(line: Line) -> Decimal:
    return round_cents(line_net(line) * tax.rate(line.tax_code))


def line_total(line: Line) -> Decimal:
    return line_net(line) + line_tax(line)


def invoice_net(invoice: Invoice) -> Decimal:
    return sum((line_net(line) for line in invoice.lines), ZERO)


def invoice_tax(invoice: Invoice) -> Decimal:
    return sum((line_tax(line) for line in invoice.lines), ZERO)


def invoice_total(invoice: Invoice) -> Decimal:
    gross = sum(
        (line.quantity * line.unit_price * (1 + tax.rate(line.tax_code)) for line in invoice.lines),
        ZERO,
    )
    return round_cents(gross)
