"""CSV input: one row per invoice line (see docs/formats.md), or one row per payment."""

import csv
import io

from ledger import validation
from ledger.models import Invoice, Line, Payment
from ledger.money import MoneyError, to_decimal
from ledger.parsers import ParseError
from ledger.timeutil import TimeError, parse_timestamp
from ledger.utils import group_by

INVOICE_COLUMNS = (
    "number",
    "customer_id",
    "issued_at",
    "currency",
    "description",
    "quantity",
    "unit_price",
    "tax_code",
)
PAYMENT_COLUMNS = ("reference", "customer_id", "amount", "received_at")


def _rows(text: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in columns if c not in (reader.fieldnames or [])]
    if missing:
        raise ParseError(f"missing columns: {', '.join(missing)}")
    return list(reader)


def parse_invoices(text: str) -> list[Invoice]:
    invoices = []
    for number, rows in group_by(_rows(text, INVOICE_COLUMNS), lambda r: r["number"]).items():
        first = rows[0]
        try:
            invoice = Invoice(
                number=number,
                customer_id=first["customer_id"],
                issued_at=parse_timestamp(first["issued_at"]),
                currency=first["currency"],
                lines=tuple(
                    Line(
                        description=row["description"],
                        quantity=to_decimal(row["quantity"]),
                        unit_price=to_decimal(row["unit_price"]),
                        tax_code=row["tax_code"] or "standard",
                    )
                    for row in rows
                ),
            )
        except (MoneyError, TimeError) as exc:
            raise ParseError(f"{number}: {exc}") from None
        problems = validation.problems(invoice)
        if problems:
            raise ParseError("; ".join(problems))
        invoices.append(invoice)
    return invoices


def parse_payments(text: str) -> list[Payment]:
    payments = []
    for row in _rows(text, PAYMENT_COLUMNS):
        try:
            payments.append(
                Payment(
                    reference=row["reference"],
                    customer_id=row["customer_id"],
                    amount=to_decimal(row["amount"]),
                    received_at=parse_timestamp(row["received_at"]),
                )
            )
        except (MoneyError, TimeError) as exc:
            raise ParseError(f"payment {row['reference']}: {exc}") from None
    return payments
