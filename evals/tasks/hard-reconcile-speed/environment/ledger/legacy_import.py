"""Deprecated: the semicolon format of ledger 0.2. Convert old files to CSV once.

Will be removed in 0.5. Nothing in ledger calls it.
"""

import warnings
from decimal import Decimal
from pathlib import Path


def read_legacy(path: Path) -> list[dict[str, object]]:
    warnings.warn("legacy_import is deprecated", DeprecationWarning, stacklevel=2)
    rows = []
    for raw in path.read_text(encoding="latin-1").splitlines()[1:]:
        number, customer, date, amount = raw.split(";")
        rows.append(
            {"number": number, "customer": customer, "date": date, "amount": Decimal(amount.replace(",", "."))}
        )
    return rows
