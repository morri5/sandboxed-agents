"""The `.ledger` store: one JSON file with every invoice and payment, timestamps in UTC."""

import json
import os
from pathlib import Path

from ledger import config, serialize
from ledger.models import Invoice, Payment
from ledger.timeutil import to_utc


class StoreError(Exception):
    pass


class LedgerStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[list[Invoice], list[Payment]]:
        if not self.path.exists():
            return [], []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("format") != config.STORE_FORMAT:
            raise StoreError(f"unsupported store format: {data.get('format')!r}")
        invoices = [serialize.invoice_from_dict(i) for i in data.get("invoices", [])]
        payments = [serialize.payment_from_dict(p) for p in data.get("payments", [])]
        return invoices, payments

    def save(self, invoices: list[Invoice], payments: list[Payment]) -> None:
        numbers = [i.number for i in invoices]
        if len(numbers) != len(set(numbers)):
            raise StoreError("duplicate invoice numbers")
        data = {
            "format": config.STORE_FORMAT,
            "invoices": [
                serialize.invoice_to_dict(i) | {"issued_at": to_utc(i.issued_at).isoformat()}
                for i in invoices
            ],
            "payments": [
                serialize.payment_to_dict(p) | {"received_at": to_utc(p.received_at).isoformat()}
                for p in payments
            ],
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def add_invoice(self, invoice: Invoice) -> None:
        invoices, payments = self.load()
        if any(i.number == invoice.number for i in invoices):
            raise StoreError(f"invoice {invoice.number} exists")
        self.save([*invoices, invoice], payments)
