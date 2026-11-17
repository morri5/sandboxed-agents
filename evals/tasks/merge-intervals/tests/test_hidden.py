from intervals import merge_intervals, total_length


def test_unsorted_input() -> None:
    assert merge_intervals([(10, 12), (1, 2), (4, 6)]) == [(1, 2), (4, 6), (10, 12)]


def test_touching_intervals_merge() -> None:
    assert merge_intervals([(1, 2), (2, 3), (3, 5)]) == [(1, 5)]


def test_contained_interval_unsorted() -> None:
    assert merge_intervals([(3, 4), (0, 10), (12, 15)]) == [(0, 10), (12, 15)]


def test_input_is_not_modified() -> None:
    data = [(6, 8), (1, 3), (2, 4)]
    merge_intervals(data)
    assert data == [(6, 8), (1, 3), (2, 4)]


def test_total_length_with_unsorted_overlaps() -> None:
    assert total_length([(5, 9), (0, 3), (2, 6)]) == 9


def test_single_interval() -> None:
    assert merge_intervals([(2, 2)]) == [(2, 2)]
