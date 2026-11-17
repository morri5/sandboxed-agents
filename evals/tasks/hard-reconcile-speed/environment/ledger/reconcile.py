"""Pairing bank payments with invoices."""

from collections.abc import Sequence
from dataclasses import dataclass

from ledger.invoicing import invoice_total
from ledger.models import Invoice, Payment


@dataclass(frozen=True)
class Match:
    invoice: str  # invoice number
    payment: str  # payment reference


@dataclass(frozen=True)
class Reconciliation:
    matches: list[Match]  # in payment order
    unmatched_payments: list[Payment]  # in payment order
    open_invoices: list[Invoice]  # in the order given


def reconcile(invoices: Sequence[Invoice], payments: Sequence[Payment]) -> Reconciliation:
    """Pair payments with invoices, one payment at a time, in the order given.

    A payment whose reference is the number of an open invoice with exactly the
    payment's amount as its total pays that invoice. Otherwise it pays the oldest
    open invoice (by issue time, then number) of the same customer whose total is
    exactly the amount. A payment that matches nothing stays unmatched, and an
    invoice is paid at most once. Invoice numbers are unique.
    """
    open_invoices = list(invoices)
    matches: list[Match] = []
    unmatched: list[Payment] = []
    for payment in payments:
        match = None
        for invoice in open_invoices:
            if invoice.number == payment.reference and invoice_total(invoice) == payment.amount:
                match = invoice
                break
        if match is None:
            candidates = [
                invoice
                for invoice in open_invoices
                if invoice.customer_id == payment.customer_id
                and invoice_total(invoice) == payment.amount
            ]
            if candidates:
                match = min(candidates, key=lambda i: (i.issued_at, i.number))
        if match is None:
            unmatched.append(payment)
            continue
        open_invoices.remove(match)
        matches.append(Match(match.number, payment.reference))
    return Reconciliation(matches, unmatched, open_invoices)
