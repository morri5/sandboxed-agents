import pytest

from lab.pricing import PRICES, cost_usd

MILLION = {
    "input_tokens": 1_000_000,
    "cache_read_input_tokens": 1_000_000,
    "output_tokens": 1_000_000,
}
# One million tokens of each kind, at DeepSeek's peak prices (checked 2026-11-16).
EXPECTED = {"deepseek-flash": 0.30 + 0.006 + 1.20, "deepseek-v4-pro": 1.32 + 0.044 + 3.96}


def test_every_priced_model_is_tested() -> None:
    assert set(PRICES) == set(EXPECTED)


@pytest.mark.parametrize("model", sorted(EXPECTED))
def test_a_million_tokens_of_each_kind(model: str) -> None:
    assert cost_usd(model, MILLION) == pytest.approx(EXPECTED[model])


def test_a_real_run() -> None:
    """The usage of a small tool task measured through the SDK with deepseek-flash."""
    usage = {"input_tokens": 9661, "cache_read_input_tokens": 18816, "output_tokens": 446}
    assert cost_usd("deepseek-flash", usage) == pytest.approx(0.003546396)


def test_cache_writes_cost_like_input_and_missing_fields_count_as_zero() -> None:
    assert cost_usd("deepseek-flash", {"cache_creation_input_tokens": 1_000_000}) == 0.30
    assert cost_usd("deepseek-flash", {"input_tokens": None}) == 0
