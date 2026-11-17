"""Pairing bank payments with invoices."""

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

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
    totals = {invoice.number: invoice_total(invoice) for invoice in invoices}
    by_number = {invoice.number: invoice for invoice in invoices}
    queues: dict[tuple[str, Decimal], deque[Invoice]] = defaultdict(deque)
    for invoice in sorted(invoices, key=lambda i: (i.issued_at, i.number)):
        queues[(invoice.customer_id, totals[invoice.number])].append(invoice)
    paid: set[str] = set()
    matches: list[Match] = []
    unmatched: list[Payment] = []
    for payment in payments:
        invoice = by_number.get(payment.reference)
        if invoice is None or invoice.number in paid or totals[invoice.number] != payment.amount:
            invoice = None
            queue = queues.get((payment.customer_id, payment.amount))
            while queue and queue[0].number in paid:
                queue.popleft()
            if queue:
                invoice = queue.popleft()
        if invoice is None:
            unmatched.append(payment)
            continue
        paid.add(invoice.number)
        matches.append(Match(invoice.number, payment.reference))
    open_invoices = [invoice for invoice in invoices if invoice.number not in paid]
    return Reconciliation(matches, unmatched, open_invoices)
