import random
import signal
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ledger.invoicing import invoice_total
from ledger.models import Invoice, Line, Payment
from ledger.reconcile import Match, Reconciliation, reconcile

CET = timezone(timedelta(hours=1))
UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=CET)
# The reference solution takes 0.35 s for the year below in a 1-vCPU grader VM (measured);
# the limit is more than 80 times that, so it never flakes, and far below the grader's timeout.
LIMIT_S = 30.0


def oracle(invoices: Sequence[Invoice], payments: Sequence[Payment]) -> Reconciliation:
    """The rules of `reconcile`, written out the slow way."""
    open_invoices = list(invoices)
    matches, unmatched = [], []
    for payment in payments:
        match = next(
            (
                i
                for i in open_invoices
                if i.number == payment.reference and invoice_total(i) == payment.amount
            ),
            None,
        )
        if match is None:
            candidates = [
                i
                for i in open_invoices
                if i.customer_id == payment.customer_id and invoice_total(i) == payment.amount
            ]
            match = min(candidates, key=lambda i: (i.issued_at, i.number), default=None)
        if match is None:
            unmatched.append(payment)
            continue
        open_invoices = [i for i in open_invoices if i.number != match.number]
        matches.append(Match(match.number, payment.reference))
    return Reconciliation(matches, unmatched, open_invoices)


def invoice(number: str, customer: str, issued: datetime, net: Decimal, code: str = "zero") -> Invoice:
    return Invoice(number, customer, issued, "EUR", (Line("Work", Decimal("1"), net, code),))


def random_case(rng: random.Random) -> tuple[list[Invoice], list[Payment]]:
    customers = [f"C-{n}" for n in range(rng.randint(1, 6))]
    nets = [Decimal(rng.choice([1000, 1999, 2500, 4200])) / 100 for _ in range(3)]
    invoices = []
    for n in rng.sample(range(1000), rng.randint(0, 60)):
        hours = rng.choice([0, 1, 2, 24, 48, 49, 500])  # repeated times: ties are broken by number
        offset = rng.choice([CET, UTC, timezone(timedelta(hours=-5))])
        issued = (BASE + timedelta(hours=hours)).astimezone(offset)
        invoices.append(
            invoice(f"INV-{n:04d}", rng.choice(customers), issued, rng.choice(nets), rng.choice(["zero", "standard"]))
        )
    payments = []
    for k in range(rng.randint(0, 80)):
        amount = invoice_total(rng.choice(invoices)) if invoices and rng.random() < 0.8 else Decimal("9.99")
        if invoices and rng.random() < 0.4:
            reference = rng.choice(invoices).number
        else:
            reference = rng.choice([f"bank {k}", "INV-9999", "see invoice"])
        payments.append(Payment(reference, rng.choice(customers + ["C-x"]), amount, BASE + timedelta(days=k)))
    return invoices, payments


def test_same_results_and_order_as_the_rules() -> None:
    rng = random.Random(2026)
    for _ in range(400):
        invoices, payments = random_case(rng)
        assert reconcile(invoices, payments) == oracle(invoices, payments)


def test_results_do_not_depend_on_the_amount_spelling() -> None:
    invoices = [invoice("INV-1", "C-1", BASE, Decimal("10.0")), invoice("INV-2", "C-1", BASE, Decimal("10"))]
    payments = [
        Payment("bank", "C-1", Decimal("10.00"), BASE),
        Payment("INV-2", "C-1", Decimal("10.000"), BASE),
    ]
    assert reconcile(invoices, payments) == oracle(invoices, payments)


def test_inputs_are_not_modified() -> None:
    rng = random.Random(5)
    invoices, payments = random_case(rng)
    before = (list(invoices), list(payments))
    reconcile(invoices, payments)
    assert (invoices, payments) == before


@contextmanager
def time_limit(seconds: float) -> Iterator[None]:
    def expired(signum: int, frame: object) -> None:
        raise TimeoutError(f"reconcile took longer than {seconds} s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_a_year_of_payments_is_fast() -> None:
    """100,000 invoices and 100,000 payments (200,000 rows)."""
    n = 100_000
    rng = random.Random(1)
    invoices = [
        invoice(
            f"INV-2026-{k:06d}",
            f"C-{k % 997}",
            BASE + timedelta(minutes=5 * k),
            Decimal(100_000 + k) / 100,
        )
        for k in range(n)
    ]
    order = list(range(n))
    rng.shuffle(order)
    payments = [
        Payment(
            invoices[k].number if k % 2 else f"bank {k}",
            invoices[k].customer_id,
            Decimal(100_000 + k) / 100,
            BASE + timedelta(days=200, seconds=k),
        )
        for k in order[: n - 1000]
    ]
    payments += [
        Payment(f"stray {k}", "C-1", Decimal("0.01"), BASE + timedelta(days=300)) for k in range(1000)
    ]
    started = time.monotonic()
    with time_limit(LIMIT_S):
        result = reconcile(invoices, payments)
    print(f"reconcile: {time.monotonic() - started:.2f} s")
    assert result.matches == [Match(invoices[k].number, payments[i].reference) for i, k in enumerate(order[: n - 1000])]
    assert result.unmatched_payments == payments[n - 1000 :]
    assert result.open_invoices == [invoices[k] for k in sorted(order[n - 1000 :])]
