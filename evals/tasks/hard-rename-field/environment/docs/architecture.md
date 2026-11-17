# Architecture

- `models`: the frozen dataclasses (`Invoice`, `Line`, `Payment`).
- `money`, `tax`, `invoicing`: amounts are `Decimal`; every line is rounded half up to the cent, net and tax separately, and an invoice's totals are sums of rounded lines.
- `parsers`: CSV and JSON input, validated by `validation`.
- `serialize`: invoices and payments to and from plain dicts (JSON input, the store and the JSON export).
- `storage`: the `.ledger` store.
- `reports`: monthly reports in a given time zone (`timeutil.month_range`).
- `reconcile`: pairs payments with invoices.
- `exporters`: export formats, each registered by name.
- `cli`: the `ledger` command.

`legacy_import` is deprecated and will be removed in 0.5.
