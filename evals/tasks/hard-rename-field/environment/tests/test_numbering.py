import unittest

from ledger.numbering import next_number


class NumberingTest(unittest.TestCase):
    def test_next_number(self) -> None:
        self.assertEqual(next_number(["INV-2026-0007", "INV-2025-0099", "x"], 2026), "INV-2026-0008")
        self.assertEqual(next_number([], 2027), "INV-2027-0001")


if __name__ == "__main__":
    unittest.main()
