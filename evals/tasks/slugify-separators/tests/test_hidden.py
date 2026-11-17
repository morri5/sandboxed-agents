from textkit import slugify


def test_runs_of_punctuation_and_spaces_collapse() -> None:
    assert slugify("Rock & Roll -- Live!!") == "rock-roll-live"


def test_no_leading_or_trailing_separator() -> None:
    assert slugify("...Ready? Set. Go...") == "ready-set-go"


def test_custom_separator() -> None:
    assert slugify("  Tea  for   Two  ", sep="_") == "tea_for_two"


def test_only_symbols_gives_an_empty_slug() -> None:
    assert slugify("!!! ???") == ""


def test_empty_string() -> None:
    assert slugify("") == ""


def test_digits_are_kept() -> None:
    assert slugify("Top 10 (2026)") == "top-10-2026"
