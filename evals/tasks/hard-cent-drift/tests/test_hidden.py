import contextlib
import csv
import io
import random
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ledger.cli import main
from ledger.invoicing import invoice_net, invoice_tax, invoice_total, line_net, line_tax, line_total
from ledger.models import Invoice, Line
from ledger.parsers import csv_parser, json_parser
from ledger.reports import monthly_report
from zoneinfo import ZoneInfo

CET = timezone(timedelta(hours=1))


def invoice(*lines: Line) -> Invoice:
    return Invoice("INV-2026-0100", "C-1", datetime(2026, 2, 2, 10, tzinfo=CET), "EUR", lines)


def test_total_is_the_sum_of_the_rounded_lines() -> None:
    inv = invoice(*[Line("Book", Decimal("1"), Decimal("10.05"), "reduced")] * 3)
    assert [line_total(line) for line in inv.lines] == [Decimal("10.75")] * 3
    assert invoice_total(inv) == Decimal("32.25")


def test_totals_add_up_on_random_invoices() -> None:
    rng = random.Random(7)
    for _ in range(500):
        lines = tuple(
            Line(
                "x",
                Decimal(rng.randint(1, 40)) / Decimal(rng.choice([1, 2, 4, 10])),
                Decimal(rng.randint(1, 100_000)) / Decimal(rng.choice([100, 1000])),
                rng.choice(["standard", "reduced", "zero"]),
            )
            for _ in range(rng.randint(1, 8))
        )
        inv = invoice(*lines)
        total = invoice_total(inv)
        assert total == sum((line_total(line) for line in lines), Decimal(0))
        assert total == invoice_net(inv) + invoice_tax(inv)
        assert isinstance(total, Decimal)
        assert total == total.quantize(Decimal("0.01"))


def test_lines_round_half_up() -> None:
    line = Line("x", Decimal("1"), Decimal("0.125"), "zero")
    assert line_net(line) == Decimal("0.13")
    line = Line("x", Decimal("1"), Decimal("10.50"), "reduced")  # tax 0.735
    assert line_tax(line) == Decimal("0.74")


JSON = """{"invoices": [{"number": "INV-2026-0200", "customer_id": "C-2",
  "issued_at": "2026-02-03T10:00:00+01:00", "currency": "EUR",
  "lines": [{"description": "a", "quantity": 1, "unit_price": 2.675, "tax_code": "zero"},
            {"description": "b", "quantity": 1, "unit_price": 1.005, "tax_code": "zero"},
            {"description": "c", "quantity": 3, "unit_price": 0.335, "tax_code": "zero"},
            {"description": "d", "quantity": 1, "unit_price": 8.15, "tax_code": "standard"}]}]}"""

CSV = """number,customer_id,issued_at,currency,description,quantity,unit_price,tax_code
INV-2026-0200,C-2,2026-02-03T10:00:00+01:00,EUR,a,1,2.675,zero
INV-2026-0200,C-2,2026-02-03T10:00:00+01:00,EUR,b,1,1.005,zero
INV-2026-0200,C-2,2026-02-03T10:00:00+01:00,EUR,c,3,0.335,zero
INV-2026-0200,C-2,2026-02-03T10:00:00+01:00,EUR,d,1,8.15,standard
"""


def test_json_numbers_are_exact() -> None:
    [inv] = json_parser.parse_invoices(JSON)
    assert [line.unit_price for line in inv.lines] == [
        Decimal("2.675"),
        Decimal("1.005"),
        Decimal("0.335"),
        Decimal("8.15"),
    ]
    assert all(isinstance(line.unit_price, Decimal) for line in inv.lines)
    assert [line_net(line) for line in inv.lines] == [
        Decimal("2.68"),
        Decimal("1.01"),
        Decimal("1.01"),
        Decimal("8.15"),
    ]
    assert line_tax(inv.lines[3]) == Decimal("1.55")  # 1.5485
    assert invoice_total(inv) == Decimal("14.40")


def test_json_and_csv_give_the_same_invoice() -> None:
    [from_json] = json_parser.parse_invoices(JSON)
    [from_csv] = csv_parser.parse_invoices(CSV)
    assert from_json == from_csv
    assert invoice_total(from_json) == invoice_total(from_csv)


def test_report_total_is_the_sum_of_invoice_totals() -> None:
    invoices = [
        Invoice(
            f"INV-2026-03{n:02d}",
            "C-1",
            datetime(2026, 2, 1 + n, 10, tzinfo=CET),
            "EUR",
            tuple(Line("Book", Decimal("1"), Decimal("10.05"), "reduced") for _ in range(n)),
        )
        for n in range(1, 6)
    ]
    report = monthly_report(invoices, 2026, 2, ZoneInfo("Europe/Berlin"))
    assert report.total == sum((invoice_total(i) for i in invoices), Decimal(0))
    assert report.total == Decimal("10.75") * 15


def test_csv_export_rows_add_up() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoices.json"
        path.write_text(JSON)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["export", str(path), "--format", "csv"]) == 0
    [header, row] = list(csv.reader(io.StringIO(out.getvalue())))
    values = dict(zip(header, row, strict=True))
    assert values["net"] == "12.85"
    assert values["tax"] == "1.55"
    assert values["total"] == "14.40"
