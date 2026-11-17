import unittest

from stock import Inventory


class InventoryTest(unittest.TestCase):
    def test_add_and_count(self) -> None:
        inv = Inventory()
        inv.add("apple", 3)
        inv.add("apple", 2)
        self.assertEqual(inv.count("apple"), 5)
        self.assertEqual(inv.count("pear"), 0)

    def test_skus_sorted(self) -> None:
        inv = Inventory()
        inv.add("b", 1)
        inv.add("a", 1)
        self.assertEqual(inv.skus(), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
