import unittest

from timefmt import format_duration


class FormatTest(unittest.TestCase):
    def test_mixed_units(self) -> None:
        self.assertEqual(format_duration(3725), "1h 2m 5s")

    def test_zero(self) -> None:
        self.assertEqual(format_duration(0), "0s")

    def test_days(self) -> None:
        self.assertEqual(format_duration(90000), "1d 1h")


if __name__ == "__main__":
    unittest.main()
