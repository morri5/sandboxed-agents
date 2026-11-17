"""Checks on parsed invoices; the parsers refuse invoices with problems."""

from decimal import Decimal

from ledger import config, tax
from ledger.models import Invoice


def problems(invoice: Invoice) -> list[str]:
    found = []
    if not invoice.lines:
        found.append("no lines")
    if invoice.currency not in config.CURRENCIES:
        found.append(f"unknown currency {invoice.currency}")
    for i, line in enumerate(invoice.lines, 1):
        if line.quantity <= 0:
            found.append(f"line {i}: quantity must be positive")
        if line.unit_price < Decimal(0):
            found.append(f"line {i}: negative unit price")
        if line.tax_code not in tax.RATES:
            found.append(f"line {i}: unknown tax code {line.tax_code}")
    return [f"{invoice.number}: {p}" for p in found]
