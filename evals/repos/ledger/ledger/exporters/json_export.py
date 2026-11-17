"""JSON export: every invoice with its lines and totals."""

import json
from collections.abc import Sequence

from ledger import serialize
from ledger.exporters import register
from ledger.invoicing import invoice_net, invoice_tax, invoice_total
from ledger.models import Invoice


@register("json")
def export_json(invoices: Sequence[Invoice]) -> str:
    items = [
        serialize.invoice_to_dict(invoice)
        | {
            "net": str(invoice_net(invoice)),
            "tax": str(invoice_tax(invoice)),
            "total": str(invoice_total(invoice)),
        }
        for invoice in invoices
    ]
    return json.dumps({"invoices": items}, indent=2) + "\n"
