"""CSV export: one row per invoice, with its totals."""

import csv
import io
from collections.abc import Sequence

from ledger.exporters import register
from ledger.invoicing import invoice_net, invoice_tax, invoice_total
from ledger.models import Invoice

COLUMNS = ("number", "customer_id", "issued_at", "currency", "net", "tax", "total")


def row(invoice: Invoice) -> list[str]:
    return [
        invoice.number,
        invoice.customer_id,
        invoice.issued_at.isoformat(),
        invoice.currency,
        str(invoice_net(invoice)),
        str(invoice_tax(invoice)),
        str(invoice_total(invoice)),
    ]


@register("csv")
def export_csv(invoices: Sequence[Invoice]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(row(invoice) for invoice in invoices)
    return out.getvalue()
