import csv
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ledger import exporters
from ledger.models import Invoice, Line

CET = timezone(timedelta(hours=1))
INVOICES = [
    Invoice(
        "INV-2026-0001",
        "C-1",
        datetime(2026, 3, 2, 9, 15, tzinfo=CET),
        "EUR",
        (
            Line("Consulting", Decimal("2"), Decimal("120.00"), "standard"),
            Line("Travel", Decimal("1"), Decimal("48.30"), "zero"),
        ),
    ),
    Invoice(
        "INV-2026-0002",
        "C-2",
        datetime(2026, 3, 3, 10, tzinfo=CET),
        "EUR",
        (Line("Workshop", Decimal("1"), Decimal("900.00"), "standard"),),
    ),
]


class ExportersTest(unittest.TestCase):
    def test_formats(self) -> None:
        for name in ("csv", "json", "text"):
            self.assertIn(name, exporters.available_formats())

    def test_unknown_format(self) -> None:
        with self.assertRaises(exporters.UnknownFormat):
            exporters.get_exporter("xlsx")

    def test_csv(self) -> None:
        rows = list(csv.reader(io.StringIO(exporters.get_exporter("csv")(INVOICES))))
        self.assertEqual(rows[0], ["number", "account_id", "issued_at", "currency", "net", "tax", "total"])
        self.assertEqual(rows[1][1], "C-1")
        self.assertEqual(rows[1][-1], "333.90")

    def test_json(self) -> None:
        data = json.loads(exporters.get_exporter("json")(INVOICES))
        self.assertEqual(data["invoices"][1]["account_id"], "C-2")
        self.assertEqual(data["invoices"][1]["total"], "1071.00")

    def test_text(self) -> None:
        lines = exporters.get_exporter("text")(INVOICES).splitlines()
        self.assertEqual(lines[0].split(), ["number", "account_id", "issued", "total"])
        self.assertEqual(lines[2].split(), ["INV-2026-0002", "C-2", "2026-03-03", "1071.00", "EUR"])


if __name__ == "__main__":
    unittest.main()
