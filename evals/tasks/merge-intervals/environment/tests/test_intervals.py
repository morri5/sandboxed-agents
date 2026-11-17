import unittest

from intervals import merge_intervals, total_length


class MergeTest(unittest.TestCase):
    def test_overlapping_sorted_input(self) -> None:
        self.assertEqual(merge_intervals([(1, 4), (2, 6), (8, 9)]), [(1, 6), (8, 9)])

    def test_empty(self) -> None:
        self.assertEqual(merge_intervals([]), [])

    def test_total_length_of_disjoint_intervals(self) -> None:
        self.assertEqual(total_length([(0, 2), (5, 6)]), 3)


if __name__ == "__main__":
    unittest.main()
