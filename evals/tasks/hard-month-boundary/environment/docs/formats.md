# File formats

## Invoices, CSV

One row per invoice line; rows with the same `number` form one invoice, in file order.

| Column | Example | Notes |
|---|---|---|
| number | INV-2026-0001 | unique per invoice |
| customer_id | C-104 | |
| issued_at | 2026-03-02T09:15:00+01:00 | ISO 8601 with an offset |
| currency | EUR | three letters |
| description | Consulting | |
| quantity | 2 | decimal |
| unit_price | 120.00 | decimal, net of tax |
| tax_code | standard | `standard`, `reduced` or `zero` |

## Invoices, JSON

```json
{"invoices": [{"number": "INV-2026-0001", "customer_id": "C-104",
  "issued_at": "2026-03-02T09:15:00+01:00", "currency": "EUR",
  "lines": [{"description": "Consulting", "quantity": 2, "unit_price": 120.00,
             "tax_code": "standard"}]}]}
```

Numbers may be JSON numbers or strings.

## Payments, CSV

`reference,customer_id,amount,received_at`. The reference is free text from the bank; when it is an invoice number, reconciliation uses it.

## Store (`.ledger`)

JSON written by `ledger.storage`: `{"format": 1, "invoices": [...], "payments": [...]}` with amounts as strings and timestamps in UTC.
