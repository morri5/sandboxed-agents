We are renaming "customer" to "account" across our products, and ledger has to follow: the `customer_id` field becomes `account_id`.

Rename it everywhere it shows up: in the invoice and payment data, in the CSV and JSON input files, in the `.ledger` store, in every export format, and in the CLI (the export filter `--customer` becomes `--account`). New files and every export use `account_id` only.

Our customers have years of old CSV and JSON files, and stores, that still say `customer_id`. Those must keep loading as before.

Only the field and the CLI flag change in this step. Other Python names in the package (for example the report's `by_customer`) stay as they are, because other teams import them.
