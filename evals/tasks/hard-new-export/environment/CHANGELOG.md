# Changelog

## 0.4.3
- `export --output FILE` writes to a file and picks the format from its suffix.

## 0.4.2
- `reconcile` prefers a payment's reference over the customer and amount.
- The text export shows the issue date instead of the full timestamp.

## 0.4.0
- Export formats are plugins registered in `ledger.exporters`.
- `ledger.legacy_import` is deprecated: convert old semicolon files to CSV once.

## 0.3.0
- Monthly reports take a time zone (`--tz`); timestamps must carry an offset.
- Amounts are `Decimal` everywhere.
