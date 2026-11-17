import pytest

from wordwrap import wrap


def test_exact_fit_but_no_more() -> None:
    assert wrap("aaa bbb ccc", 6) == ["aaa", "bbb", "ccc"]
    assert wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]


def test_no_line_exceeds_width() -> None:
    text = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod"
    for width in range(5, 30):
        lines = wrap(text, width)
        assert all(len(line) <= width for line in lines), (width, lines)
        if width >= max(len(word) for word in text.split()):  # no word had to be split
            assert " ".join(lines) == text


def test_long_word_is_split() -> None:
    assert wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_long_word_between_short_ones() -> None:
    assert wrap("go supercalifragilistic now", 8) == ["go", "supercal", "ifragili", "stic now"]


def test_whitespace_is_normalised() -> None:
    assert wrap("  one\ttwo\n\nthree  ", 20) == ["one two three"]


def test_bad_width() -> None:
    with pytest.raises(ValueError):
        wrap("text", 0)


def test_blank_text() -> None:
    assert wrap(" \n ", 5) == []
