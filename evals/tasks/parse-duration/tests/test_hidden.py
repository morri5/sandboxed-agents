import pytest

from timefmt import format_duration, parse_duration


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("45s", 45), ("2m", 120), ("1h30m", 5400), ("2d 3h", 183600), ("  10m 5s ", 605), ("0s", 0)],
)
def test_valid(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "   ", "15", "3x", "1m 1h", "1h 1h", "-5s", "1.5h", "h"])
def test_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_round_trip() -> None:
    for n in (0, 1, 59, 60, 61, 3599, 3600, 86399, 86400, 93784, 1_000_000):
        assert parse_duration(format_duration(n)) == n
