import pytest

from versions import compare_versions, latest

# The precedence example from the SemVer 2.0.0 spec, lowest first.
CHAIN = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
    "1.0.1",
    "1.2.0",
    "1.10.0",
    "2.0.0",
    "10.0.0",
]


def test_numeric_parts_compare_as_numbers() -> None:
    assert compare_versions("1.10.0", "1.9.0") == 1
    assert compare_versions("0.0.9", "0.0.10") == -1
    assert compare_versions("10.0.0", "9.99.99") == 1


@pytest.mark.parametrize("i", range(len(CHAIN) - 1))
def test_spec_precedence_chain(i: int) -> None:
    assert compare_versions(CHAIN[i], CHAIN[i + 1]) == -1
    assert compare_versions(CHAIN[i + 1], CHAIN[i]) == 1


def test_equal_pre_releases() -> None:
    assert compare_versions("3.1.4-rc.2", "3.1.4-rc.2") == 0


def test_latest_with_double_digits_and_pre_releases() -> None:
    assert latest(["1.9.0", "1.10.0-beta", "1.10.0", "1.2.0"]) == "1.10.0"
    assert latest(["2.0.0-rc.1", "1.99.0", "2.0.0-alpha"]) == "2.0.0-rc.1"


def test_equal_releases() -> None:
    assert compare_versions("4.5.6", "4.5.6") == 0
