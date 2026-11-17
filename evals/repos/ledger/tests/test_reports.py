import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from ledger.models import Invoice, Line
from ledger.reports import invoices_in_month, monthly_report, render
from ledger.timeutil import month_range

CET = timezone(timedelta(hours=1))
BERLIN = ZoneInfo("Europe/Berlin")


def invoice(number: str, customer: str, issued: datetime, price: str) -> Invoice:
    return Invoice(number, customer, issued, "EUR", (Line("Work", Decimal("1"), Decimal(price), "standard"),))


INVOICES = [
    invoice("INV-2026-0001", "C-1", datetime(2026, 1, 10, 9, tzinfo=CET), "100.00"),
    invoice("INV-2026-0002", "C-2", datetime(2026, 1, 20, 9, tzinfo=CET), "50.00"),
    invoice("INV-2026-0003", "C-1", datetime(2026, 2, 3, 9, tzinfo=CET), "10.00"),
]


class ReportsTest(unittest.TestCase):
    def test_month_range(self) -> None:
        start, end = month_range(2026, 1, BERLIN)
        self.assertEqual(start, datetime(2025, 12, 31, 23, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 1, 31, 23, tzinfo=UTC))

    def test_invoices_in_month(self) -> None:
        found = invoices_in_month(INVOICES, 2026, 1, BERLIN)
        self.assertEqual([i.number for i in found], ["INV-2026-0001", "INV-2026-0002"])

    def test_totals(self) -> None:
        report = monthly_report(INVOICES, 2026, 1, BERLIN)
        self.assertEqual(report.net, Decimal("150.00"))
        self.assertEqual(report.tax, Decimal("28.50"))
        self.assertEqual(report.total, Decimal("178.50"))

    def test_render(self) -> None:
        text = render(monthly_report(INVOICES, 2026, 2, BERLIN))
        self.assertIn("INV-2026-0003", text)
        self.assertIn("11.90 EUR", text)


if __name__ == "__main__":
    unittest.main()
