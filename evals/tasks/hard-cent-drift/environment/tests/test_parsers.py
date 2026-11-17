import unittest
from decimal import Decimal

from ledger.parsers import ParseError, csv_parser, json_parser

CSV = """number,customer_id,issued_at,currency,description,quantity,unit_price,tax_code
INV-2026-0001,C-1,2026-03-02T09:15:00+01:00,EUR,Consulting,2,120.00,standard
INV-2026-0001,C-1,2026-03-02T09:15:00+01:00,EUR,Travel,1,48.30,zero
INV-2026-0002,C-2,2026-03-03T10:00:00+01:00,EUR,Workshop,1,900.00,standard
"""

JSON = """{"invoices": [{"number": "INV-2026-0003", "customer_id": "C-3",
  "issued_at": "2026-03-04T10:00:00+01:00", "currency": "EUR",
  "lines": [{"description": "Consulting", "quantity": 3, "unit_price": 120.00,
             "tax_code": "standard"},
            {"description": "Report", "quantity": "1", "unit_price": "19.99",
             "tax_code": "reduced"}]}]}"""

PAYMENTS = """reference,customer_id,amount,received_at
INV-2026-0001,C-1,333.90,2026-03-10T08:00:00+01:00
"""


class CsvParserTest(unittest.TestCase):
    def test_rows_group_into_invoices(self) -> None:
        invoices = csv_parser.parse_invoices(CSV)
        self.assertEqual([i.number for i in invoices], ["INV-2026-0001", "INV-2026-0002"])
        self.assertEqual(len(invoices[0].lines), 2)
        self.assertEqual(invoices[0].customer_id, "C-1")
        self.assertEqual(invoices[0].lines[1].unit_price, Decimal("48.30"))

    def test_missing_column(self) -> None:
        with self.assertRaises(ParseError):
            csv_parser.parse_invoices("number,issued_at\nINV-1,2026-03-02T09:15:00+01:00\n")

    def test_naive_timestamp_is_refused(self) -> None:
        with self.assertRaises(ParseError):
            csv_parser.parse_invoices(CSV.replace("2026-03-03T10:00:00+01:00", "2026-03-03T10:00:00"))

    def test_payments(self) -> None:
        [payment] = csv_parser.parse_payments(PAYMENTS)
        self.assertEqual(payment.amount, Decimal("333.90"))
        self.assertEqual(payment.customer_id, "C-1")


class JsonParserTest(unittest.TestCase):
    def test_invoices(self) -> None:
        [invoice] = json_parser.parse_invoices(JSON)
        self.assertEqual(invoice.customer_id, "C-3")
        self.assertEqual(invoice.lines[0].quantity, Decimal(3))
        self.assertEqual(invoice.lines[1].unit_price, Decimal("19.99"))

    def test_invalid_invoice_is_refused(self) -> None:
        with self.assertRaises(ParseError):
            json_parser.parse_invoices(JSON.replace('"EUR"', '"XYZ"'))

    def test_not_json(self) -> None:
        with self.assertRaises(ParseError):
            json_parser.parse_invoices("invoices:")


if __name__ == "__main__":
    unittest.main()
