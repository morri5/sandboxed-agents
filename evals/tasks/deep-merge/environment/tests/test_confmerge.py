import unittest

from confmerge import merge


class MergeTest(unittest.TestCase):
    def test_flat_override(self) -> None:
        self.assertEqual(merge({"a": 1, "b": 2}, {"b": 3}), {"a": 1, "b": 3})

    def test_new_keys_are_added(self) -> None:
        self.assertEqual(merge({"a": 1}, {"c": 4}), {"a": 1, "c": 4})


if __name__ == "__main__":
    unittest.main()
