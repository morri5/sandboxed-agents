"""Input files: invoices and payments from CSV or JSON, chosen by the file suffix."""

from pathlib import Path

from ledger.models import Invoice, Payment


class ParseError(ValueError):
    pass


def load_invoices(path: Path) -> list[Invoice]:
    from ledger.parsers import csv_parser, json_parser
    from ledger.storage import LedgerStore

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".csv":
        return csv_parser.parse_invoices(text)
    if path.suffix == ".json":
        return json_parser.parse_invoices(text)
    if path.suffix == ".ledger":
        return LedgerStore(path).load()[0]
    raise ParseError(f"unknown file type: {path.name}")


def load_payments(path: Path) -> list[Payment]:
    from ledger.parsers import csv_parser, json_parser
    from ledger.storage import LedgerStore

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".csv":
        return csv_parser.parse_payments(text)
    if path.suffix == ".json":
        return json_parser.parse_payments(text)
    if path.suffix == ".ledger":
        return LedgerStore(path).load()[1]
    raise ParseError(f"unknown file type: {path.name}")
