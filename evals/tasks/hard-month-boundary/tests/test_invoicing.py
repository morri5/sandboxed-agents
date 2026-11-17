import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ledger.invoicing import invoice_net, invoice_tax, invoice_total, line_net, line_tax
from ledger.models import Invoice, Line

CET = timezone(timedelta(hours=1))


def invoice(*lines: Line) -> Invoice:
    return Invoice("INV-2026-0001", "C-1", datetime(2026, 1, 5, 9, tzinfo=CET), "EUR", lines)


class InvoicingTest(unittest.TestCase):
    def test_line_amounts(self) -> None:
        line = Line("Consulting", Decimal("2"), Decimal("120.00"), "standard")
        self.assertEqual(line_net(line), Decimal("240.00"))
        self.assertEqual(line_tax(line), Decimal("45.60"))

    def test_line_rounds_half_up(self) -> None:
        line = Line("Book", Decimal("3"), Decimal("24.90"), "reduced")
        self.assertEqual(line_tax(line), Decimal("5.23"))

    def test_single_line_invoice(self) -> None:
        inv = invoice(Line("Workshop", Decimal("1"), Decimal("900.00"), "standard"))
        self.assertEqual(invoice_net(inv), Decimal("900.00"))
        self.assertEqual(invoice_tax(inv), Decimal("171.00"))
        self.assertEqual(invoice_total(inv), Decimal("1071.00"))

    def test_zero_rated_lines(self) -> None:
        inv = invoice(
            Line("Travel", Decimal("1"), Decimal("48.30"), "zero"),
            Line("Parking", Decimal("2"), Decimal("4.50"), "zero"),
        )
        self.assertEqual(invoice_total(inv), Decimal("57.30"))


if __name__ == "__main__":
    unittest.main()
