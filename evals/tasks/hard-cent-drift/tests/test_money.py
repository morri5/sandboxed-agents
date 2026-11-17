import unittest
from decimal import Decimal

from ledger.money import MoneyError, format_amount, round_cents, to_decimal


class MoneyTest(unittest.TestCase):
    def test_to_decimal(self) -> None:
        self.assertEqual(to_decimal(" 12.50 "), Decimal("12.50"))
        self.assertEqual(to_decimal(3), Decimal(3))

    def test_to_decimal_refuses_garbage(self) -> None:
        with self.assertRaises(MoneyError):
            to_decimal("twelve")

    def test_round_half_up(self) -> None:
        self.assertEqual(round_cents(Decimal("0.125")), Decimal("0.13"))
        self.assertEqual(round_cents(Decimal("0.135")), Decimal("0.14"))

    def test_format_amount(self) -> None:
        self.assertEqual(format_amount(Decimal("1234.5"), "EUR"), "1,234.50 EUR")


if __name__ == "__main__":
    unittest.main()
