"""JSON input: `{"invoices": [...]}` and `{"payments": [...]}` (see docs/formats.md)."""

import json
from typing import Any

from ledger import serialize, validation
from ledger.models import Invoice, Payment
from ledger.money import MoneyError
from ledger.parsers import ParseError
from ledger.timeutil import TimeError


def _load(text: str, key: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from None
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ParseError(f"expected an object with a list under {key!r}")
    return items


def parse_invoices(text: str) -> list[Invoice]:
    invoices = []
    for item in _load(text, "invoices"):
        try:
            invoice = serialize.invoice_from_dict(item)
        except (KeyError, MoneyError, TimeError) as exc:
            raise ParseError(f"invoice {item.get('number')}: {exc}") from None
        problems = validation.problems(invoice)
        if problems:
            raise ParseError("; ".join(problems))
        invoices.append(invoice)
    return invoices


def parse_payments(text: str) -> list[Payment]:
    payments = []
    for item in _load(text, "payments"):
        try:
            payments.append(serialize.payment_from_dict(item))
        except (KeyError, MoneyError, TimeError) as exc:
            raise ParseError(f"payment {item.get('reference')}: {exc}") from None
    return payments
