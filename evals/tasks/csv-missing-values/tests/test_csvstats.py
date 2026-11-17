import unittest

from csvstats import column_max, column_mean, read_rows

DATA = "city,temp\nOslo,4\nRome,18\nCairo,29\n"


class StatsTest(unittest.TestCase):
    def test_mean(self) -> None:
        self.assertAlmostEqual(column_mean(read_rows(DATA), "temp"), 17.0)

    def test_max(self) -> None:
        self.assertEqual(column_max(read_rows(DATA), "temp"), 29.0)


if __name__ == "__main__":
    unittest.main()
