import unittest

from calc import add, average, subtract


class CalcTest(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_subtract(self) -> None:
        self.assertEqual(subtract(5, 3), 2)

    def test_average_of_integers(self) -> None:
        self.assertEqual(average([1, 2, 3, 4]), 2.5)

    def test_average_of_empty_list(self) -> None:
        with self.assertRaises(ValueError):
            average([])


if __name__ == "__main__":
    unittest.main()
