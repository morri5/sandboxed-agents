import pytest

from csvstats import column_max, column_mean, read_rows


def test_mean_skips_empty_cells() -> None:
    rows = read_rows("a,b\n1,10\n2,\n3,40\n")
    assert column_mean(rows, "b") == pytest.approx(25.0)


def test_whitespace_cells_are_missing_and_values_may_be_padded() -> None:
    rows = read_rows("v\n 7 \n   \n-3\n")
    assert column_mean(rows, "v") == pytest.approx(2.0)
    assert column_max(rows, "v") == pytest.approx(7.0)


def test_max_skips_empty_cells_with_negative_values() -> None:
    rows = read_rows("k,v\na,-5\nb,\nc,-2\n")
    assert column_max(rows, "v") == pytest.approx(-2.0)


def test_no_values_gives_none() -> None:
    rows = read_rows("v\n\n \n")
    assert column_mean(rows, "v") is None
    assert column_max(rows, "v") is None


def test_no_rows_gives_none() -> None:
    rows = read_rows("v\n")
    assert column_mean(rows, "v") is None
    assert column_max(rows, "v") is None


def test_not_a_number_raises() -> None:
    with pytest.raises(ValueError):
        column_mean(read_rows("v\n1\nabc\n"), "v")
