import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ledger.models import Invoice, Line, Payment
from ledger.reconcile import Match, reconcile

CET = timezone(timedelta(hours=1))


def invoice(number: str, customer: str, day: int, net: str) -> Invoice:
    return Invoice(
        number, customer, datetime(2026, 2, day, 9, tzinfo=CET), "EUR", (Line("Work", Decimal("1"), Decimal(net), "zero"),)
    )


def payment(reference: str, customer: str, amount: str) -> Payment:
    return Payment(reference, customer, Decimal(amount), datetime(2026, 3, 1, 12, tzinfo=CET))


class ReconcileTest(unittest.TestCase):
    def test_reference_wins(self) -> None:
        invoices = [invoice("INV-1", "C-1", 1, "10.00"), invoice("INV-2", "C-1", 2, "10.00")]
        result = reconcile(invoices, [payment("INV-2", "C-1", "10.00")])
        self.assertEqual(result.matches, [Match("INV-2", "INV-2")])
        self.assertEqual([i.number for i in result.open_invoices], ["INV-1"])

    def test_oldest_invoice_of_the_customer(self) -> None:
        invoices = [invoice("INV-2", "C-1", 5, "10.00"), invoice("INV-1", "C-1", 3, "10.00")]
        result = reconcile(invoices, [payment("bank 1", "C-1", "10.00")])
        self.assertEqual(result.matches, [Match("INV-1", "bank 1")])

    def test_unmatched(self) -> None:
        result = reconcile([invoice("INV-1", "C-1", 1, "10.00")], [payment("x", "C-2", "10.00")])
        self.assertEqual(result.matches, [])
        self.assertEqual([p.reference for p in result.unmatched_payments], ["x"])
        self.assertEqual([i.number for i in result.open_invoices], ["INV-1"])


if __name__ == "__main__":
    unittest.main()
