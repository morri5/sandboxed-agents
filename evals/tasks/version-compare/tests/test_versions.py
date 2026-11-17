import unittest

from versions import compare_versions, latest


class CompareTest(unittest.TestCase):
    def test_equal(self) -> None:
        self.assertEqual(compare_versions("1.2.3", "1.2.3"), 0)

    def test_patch_and_minor(self) -> None:
        self.assertEqual(compare_versions("1.2.3", "1.2.4"), -1)
        self.assertEqual(compare_versions("1.3.0", "1.2.9"), 1)

    def test_latest(self) -> None:
        self.assertEqual(latest(["0.1.0", "0.3.0", "0.2.0"]), "0.3.0")


if __name__ == "__main__":
    unittest.main()
