import contextlib
import csv
import inspect
import io
import re
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import ledger
from ledger import exporters
from ledger.cli import main
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
        "ACME|EU",
        datetime(2026, 3, 3, 10, tzinfo=CET),
        "EUR",
        (Line("Workshop", Decimal("1"), Decimal("900.00"), "standard"),),
    ),
]
SOURCE = (
    "number,customer_id,issued_at,currency,description,quantity,unit_price,tax_code\n"
    "INV-2026-0001,C-1,2026-03-02T09:15:00+01:00,EUR,Consulting,2,120.00,standard\n"
    "INV-2026-0001,C-1,2026-03-02T09:15:00+01:00,EUR,Travel,1,48.30,zero\n"
)
SEPARATOR = re.compile(r"^\s*:?-{3,}:?\s*$")


def cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    parts = re.split(r"(?<!\\)\|", line)
    return [p.strip().replace("\\|", "|").replace("&#124;", "|").replace("&vert;", "|") for p in parts]


def table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) >= 2, text
    assert all(SEPARATOR.match(c) for c in cells(lines[1])), lines[1]
    return cells(lines[0]), [cells(line) for line in lines[2:]]


def csv_table(invoices: list[Invoice]) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(exporters.get_exporter("csv")(invoices))))
    return rows[0], rows[1:]


def test_markdown_is_registered() -> None:
    assert "markdown" in exporters.available_formats()
    assert callable(exporters.get_exporter("markdown"))


def test_same_columns_and_values_as_csv() -> None:
    header, rows = table(exporters.get_exporter("markdown")(INVOICES))
    assert (header, rows) == csv_table(INVOICES)


def test_pipe_in_a_value_does_not_break_the_table() -> None:
    _, rows = table(exporters.get_exporter("markdown")(INVOICES))
    assert rows[1][1] == "ACME|EU"
    assert len(rows[1]) == len(rows[0])


def test_no_invoices_gives_the_header_only() -> None:
    header, rows = table(exporters.get_exporter("markdown")([]))
    assert header == csv_table([])[0]
    assert rows == []


def test_cli_export_and_help() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source.csv"
        source.write_text(SOURCE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["export", str(source), "--format", "markdown"]) == 0
    assert table(out.getvalue()) == csv_table(INVOICES[:1])
    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text), pytest.raises(SystemExit):
        main(["export", "--help"])
    assert "markdown" in help_text.getvalue()


def test_amount_columns_are_right_aligned() -> None:
    lines = exporters.get_exporter("markdown")(INVOICES).splitlines()
    header, rule = cells(lines[0]), cells(lines[1])
    aligned = dict(zip(header, rule, strict=True))
    for column in ("net", "tax", "total"):
        assert re.fullmatch(r":?-{3,}:", aligned[column]), (column, aligned[column])
    for column in ("number", "customer_id"):
        assert not re.fullmatch(r"-{3,}:", aligned[column]), (column, aligned[column])


def test_output_file_with_md_suffix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source.csv"
        source.write_text(SOURCE)
        target = Path(tmp) / "wiki.md"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["export", str(source), "--output", str(target)]) == 0
        assert out.getvalue() == ""
        assert table(target.read_text()) == csv_table(INVOICES[:1])
    assert exporters.format_for(Path("wiki.md")) == "markdown"


def test_existing_formats_and_api_are_unchanged() -> None:
    assert {"csv", "json", "text"} <= set(exporters.available_formats())
    assert list(inspect.signature(exporters.register).parameters) == ["name", "suffix"]
    assert list(inspect.signature(exporters.format_for).parameters) == ["path"]
    assert exporters.format_for(Path("a.csv")) == "csv"
    assert exporters.format_for(Path("a.json")) == "json"
    assert list(inspect.signature(exporters.get_exporter).parameters) == ["name"]
    assert list(inspect.signature(exporters.available_formats).parameters) == []
    assert csv_table(INVOICES)[0] == ["number", "customer_id", "issued_at", "currency", "net", "tax", "total"]
    with pytest.raises(exporters.UnknownFormat):
        exporters.get_exporter("xlsx")


def test_no_new_dependency() -> None:
    pyproject = Path(ledger.__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"].get("dependencies", []) == []
