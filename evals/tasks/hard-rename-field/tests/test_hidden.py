import contextlib
import csv
import dataclasses
import io
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ledger import exporters
from ledger.cli import main
from ledger.models import Invoice, Line, Payment
from ledger.parsers import csv_parser, json_parser, load_invoices
from ledger.reconcile import Match, reconcile
from ledger.storage import LedgerStore

CET = timezone(timedelta(hours=1))
INVOICE_CSV = """number,{key},issued_at,currency,description,quantity,unit_price,tax_code
INV-2026-0001,A-1,2026-03-02T09:15:00+01:00,EUR,Consulting,2,120.00,standard
INV-2026-0001,A-1,2026-03-02T09:15:00+01:00,EUR,Travel,1,48.30,zero
INV-2026-0002,A-2,2026-03-03T10:00:00+01:00,EUR,Workshop,1,900.00,standard
"""
PAYMENT_CSV = """reference,{key},amount,received_at
bank 7,A-2,1071.00,2026-03-10T08:00:00+01:00
"""
INVOICE_JSON = """{{"invoices": [{{"number": "INV-2026-0003", "{key}": "A-3",
  "issued_at": "2026-03-04T10:00:00+01:00", "currency": "EUR",
  "lines": [{{"description": "Report", "quantity": 1, "unit_price": "19.99",
             "tax_code": "reduced"}}]}}]}}"""
PAYMENT_JSON = """{{"payments": [{{"reference": "bank 8", "{key}": "A-3", "amount": "21.39",
  "received_at": "2026-03-11T10:00:00+01:00"}}]}}"""


def invoice(number: str = "INV-2026-0009", account: str = "A-9") -> Invoice:
    return Invoice(
        number=number,
        account_id=account,
        issued_at=datetime(2026, 3, 5, 12, tzinfo=CET),
        currency="EUR",
        lines=(Line("Hosting", Decimal("1"), Decimal("35.00"), "standard"),),
    )


def test_models_have_account_id_only() -> None:
    for model in (Invoice, Payment):
        names = {f.name for f in dataclasses.fields(model)}
        assert "account_id" in names
        assert "customer_id" not in names
    assert invoice().account_id == "A-9"


def test_csv_with_the_new_column() -> None:
    invoices = csv_parser.parse_invoices(INVOICE_CSV.format(key="account_id"))
    assert [(i.number, i.account_id) for i in invoices] == [("INV-2026-0001", "A-1"), ("INV-2026-0002", "A-2")]
    [payment] = csv_parser.parse_payments(PAYMENT_CSV.format(key="account_id"))
    assert payment.account_id == "A-2"


def test_csv_with_the_old_column() -> None:
    invoices = csv_parser.parse_invoices(INVOICE_CSV.format(key="customer_id"))
    assert [(i.number, i.account_id) for i in invoices] == [("INV-2026-0001", "A-1"), ("INV-2026-0002", "A-2")]
    assert len(invoices[0].lines) == 2
    [payment] = csv_parser.parse_payments(PAYMENT_CSV.format(key="customer_id"))
    assert payment.account_id == "A-2"


def test_json_with_the_new_and_the_old_key() -> None:
    for key in ("account_id", "customer_id"):
        [inv] = json_parser.parse_invoices(INVOICE_JSON.format(key=key))
        assert inv.account_id == "A-3"
        [payment] = json_parser.parse_payments(PAYMENT_JSON.format(key=key))
        assert payment.account_id == "A-3"


def test_load_invoices_from_old_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_csv = Path(tmp) / "old.csv"
        old_csv.write_text(INVOICE_CSV.format(key="customer_id"))
        old_json = Path(tmp) / "old.json"
        old_json.write_text(INVOICE_JSON.format(key="customer_id"))
        assert [i.account_id for i in load_invoices(old_csv)] == ["A-1", "A-2"]
        assert [i.account_id for i in load_invoices(old_json)] == ["A-3"]


def test_store_writes_the_new_key() -> None:
    payment = Payment("bank 9", "A-9", Decimal("41.65"), datetime(2026, 3, 6, tzinfo=CET))
    with tempfile.TemporaryDirectory() as tmp:
        store = LedgerStore(Path(tmp) / "books.ledger")
        store.save([invoice()], [payment])
        text = store.path.read_text()
        assert "account_id" in text
        assert "customer_id" not in text
        invoices, payments = store.load()
    assert invoices == [invoice()]
    assert payments == [payment]


def test_store_reads_files_written_before_the_rename() -> None:
    old = {
        "format": 1,
        "invoices": [
            {
                "number": "INV-2026-0042",
                "customer_id": "C-7",
                "issued_at": "2026-06-01T07:30:00+00:00",
                "currency": "EUR",
                "lines": [{"description": "Hosting", "quantity": "1", "unit_price": "35.00", "tax_code": "standard"}],
            }
        ],
        "payments": [
            {"reference": "INV-2026-0042", "customer_id": "C-7", "amount": "41.65", "received_at": "2026-06-03T10:00:00+00:00"}
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.ledger"
        path.write_text(json.dumps(old))
        invoices, payments = LedgerStore(path).load()
    assert [(i.number, i.account_id) for i in invoices] == [("INV-2026-0042", "C-7")]
    assert [(p.reference, p.account_id) for p in payments] == [("INV-2026-0042", "C-7")]
    assert reconcile(invoices, payments).matches == [Match("INV-2026-0042", "INV-2026-0042")]


def test_every_export_format_uses_the_new_name() -> None:
    invoices = [invoice("INV-2026-0010", "A-10")]
    assert {"csv", "json", "text"} <= set(exporters.available_formats())
    for name in exporters.available_formats():
        text = exporters.get_exporter(name)(invoices)
        assert "customer" not in text, name
        assert "account_id" in text, name
        assert "A-10" in text, name
    rows = list(csv.reader(io.StringIO(exporters.get_exporter("csv")(invoices))))
    assert rows[0][1] == "account_id"
    assert rows[1][1] == "A-10"
    data = json.loads(exporters.get_exporter("json")(invoices))
    assert data["invoices"][0]["account_id"] == "A-10"
    assert "customer_id" not in data["invoices"][0]


def test_reconcile_by_account() -> None:
    payments = [Payment("bank 1", "A-9", Decimal("41.65"), datetime(2026, 3, 7, tzinfo=CET))]
    assert reconcile([invoice()], payments).matches == [Match("INV-2026-0009", "bank 1")]


def test_cli_filters_by_account() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoices.csv"
        path.write_text(INVOICE_CSV.format(key="customer_id"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["export", str(path), "--format", "csv", "--account", "A-2"]) == 0
    rows = list(csv.reader(io.StringIO(out.getvalue())))
    assert rows[0][1] == "account_id"
    assert [r[0] for r in rows[1:]] == ["INV-2026-0002"]
