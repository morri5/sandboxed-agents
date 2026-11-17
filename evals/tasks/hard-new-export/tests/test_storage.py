import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ledger.models import Invoice, Line, Payment
from ledger.storage import LedgerStore, StoreError

CEST = timezone(timedelta(hours=2))
INVOICE = Invoice(
    "INV-2026-0042",
    "C-7",
    datetime(2026, 6, 1, 9, 30, tzinfo=CEST),
    "EUR",
    (Line("Hosting", Decimal("1"), Decimal("35.00"), "standard"),),
)
PAYMENT = Payment("INV-2026-0042", "C-7", Decimal("41.65"), datetime(2026, 6, 3, 12, tzinfo=CEST))


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "books.ledger"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_round_trip(self) -> None:
        store = LedgerStore(self.path)
        store.save([INVOICE], [PAYMENT])
        invoices, payments = store.load()
        self.assertEqual(invoices, [INVOICE])
        self.assertEqual(payments, [PAYMENT])

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(LedgerStore(self.path).load(), ([], []))

    def test_duplicate_number(self) -> None:
        store = LedgerStore(self.path)
        store.add_invoice(INVOICE)
        with self.assertRaises(StoreError):
            store.add_invoice(INVOICE)


if __name__ == "__main__":
    unittest.main()
