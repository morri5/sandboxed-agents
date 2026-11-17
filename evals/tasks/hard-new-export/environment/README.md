# ledger

Invoices, payments and monthly reports for a small business, with the standard library only.

```sh
python -m ledger report examples/invoices.csv --month 2026-03 --tz Europe/Berlin
python -m ledger export examples/invoices.csv --format csv
python -m ledger export examples/invoices.csv --output march.json
python -m ledger reconcile examples/invoices.csv examples/payments.csv
python -m ledger check examples/invoices.json
```

Input files are CSV or JSON (see [docs/formats.md](docs/formats.md)); a `.ledger` file is the app's own store. How the pieces fit together is in [docs/architecture.md](docs/architecture.md).

Run the tests:

```sh
python -m unittest -v
```
