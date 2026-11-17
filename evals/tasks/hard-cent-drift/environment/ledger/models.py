"""The domain model. Amounts are `Decimal`, timestamps are timezone-aware."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal  # net of tax
    tax_code: str = "standard"


@dataclass(frozen=True)
class Invoice:
    number: str
    customer_id: str
    issued_at: datetime
    currency: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Payment:
    reference: str  # free text from the bank; often an invoice number
    customer_id: str
    amount: Decimal
    received_at: datetime
