"""Monthly reports: the invoices issued in a calendar month of a given time zone."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import tzinfo
from decimal import Decimal

from ledger.invoicing import invoice_net, invoice_tax, invoice_total
from ledger.models import Invoice
from ledger.money import format_amount
from ledger.timeutil import month_range


@dataclass
class MonthlyReport:
    year: int
    month: int
    currency: str
    invoices: list[Invoice] = field(default_factory=list)
    net: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    by_customer: dict[str, Decimal] = field(default_factory=dict)


def invoices_in_month(
    invoices: Iterable[Invoice], year: int, month: int, tz: tzinfo
) -> list[Invoice]:
    start, end = month_range(year, month, tz)
    return [i for i in invoices if start <= i.issued_at < end]


def monthly_report(
    invoices: Iterable[Invoice], year: int, month: int, tz: tzinfo, currency: str = "EUR"
) -> MonthlyReport:
    report = MonthlyReport(year, month, currency)
    for invoice in invoices_in_month(invoices, year, month, tz):
        if invoice.currency != currency:
            continue
        report.invoices.append(invoice)
        report.net += invoice_net(invoice)
        report.tax += invoice_tax(invoice)
        report.total += invoice_total(invoice)
        report.by_customer[invoice.customer_id] = (
            report.by_customer.get(invoice.customer_id, Decimal("0.00")) + invoice_total(invoice)
        )
    return report


def render(report: MonthlyReport) -> str:
    lines = [f"Report {report.year}-{report.month:02d} ({report.currency})", ""]
    for invoice in sorted(report.invoices, key=lambda i: (i.issued_at, i.number)):
        lines.append(
            f"{invoice.number}  {invoice.customer_id:<8}"
            f"  {format_amount(invoice_total(invoice), invoice.currency):>16}"
        )
    lines += [
        "",
        f"Net:   {format_amount(report.net, report.currency):>16}",
        f"Tax:   {format_amount(report.tax, report.currency):>16}",
        f"Total: {format_amount(report.total, report.currency):>16}",
    ]
    return "\n".join(lines)
