"""Plain-text export: an aligned table for the terminal."""

from collections.abc import Sequence

from ledger.exporters import register
from ledger.invoicing import invoice_total
from ledger.models import Invoice

HEADER = ("number", "customer_id", "issued", "total")


@register("text")
def export_text(invoices: Sequence[Invoice]) -> str:
    rows = [HEADER] + [
        (i.number, i.customer_id, i.issued_at.date().isoformat(), f"{invoice_total(i)} {i.currency}")
        for i in invoices
    ]
    widths = [max(len(r[c]) for r in rows) for c in range(len(HEADER))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    return "\n".join(lines) + "\n"
