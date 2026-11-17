"""Invoices and payments to and from plain dicts (JSON input, the store, the JSON export)."""

from typing import Any

from ledger.models import Invoice, Line, Payment
from ledger.money import to_decimal
from ledger.timeutil import parse_timestamp


def line_to_dict(line: Line) -> dict[str, Any]:
    return {
        "description": line.description,
        "quantity": str(line.quantity),
        "unit_price": str(line.unit_price),
        "tax_code": line.tax_code,
    }


def invoice_to_dict(invoice: Invoice) -> dict[str, Any]:
    return {
        "number": invoice.number,
        "customer_id": invoice.customer_id,
        "issued_at": invoice.issued_at.isoformat(),
        "currency": invoice.currency,
        "lines": [line_to_dict(line) for line in invoice.lines],
    }


def line_from_dict(data: dict[str, Any]) -> Line:
    return Line(
        description=str(data.get("description", "")),
        quantity=to_decimal(data["quantity"]),
        unit_price=to_decimal(data["unit_price"]),
        tax_code=str(data.get("tax_code", "standard")),
    )


def invoice_from_dict(data: dict[str, Any]) -> Invoice:
    return Invoice(
        number=str(data["number"]),
        customer_id=str(data["customer_id"]),
        issued_at=parse_timestamp(data["issued_at"]),
        currency=str(data.get("currency", "EUR")),
        lines=tuple(line_from_dict(line) for line in data["lines"]),
    )


def payment_to_dict(payment: Payment) -> dict[str, Any]:
    return {
        "reference": payment.reference,
        "customer_id": payment.customer_id,
        "amount": str(payment.amount),
        "received_at": payment.received_at.isoformat(),
    }


def payment_from_dict(data: dict[str, Any]) -> Payment:
    return Payment(
        reference=str(data["reference"]),
        customer_id=str(data["customer_id"]),
        amount=to_decimal(data["amount"]),
        received_at=parse_timestamp(data["received_at"]),
    )
