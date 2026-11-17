"""Eval statistics against references that can be worked out by hand."""

import math

import pytest

from lab.evals.stats import (
    bootstrap_ci,
    mean_over_tasks,
    paired_difference,
    pass_at_k,
    pass_hat_k,
    solve_rate,
)

N = 3  # trials per task
# 300 synthetic tasks, 75 each with 0, 1, 2 and 3 of 3 trials solved.
RESULTS = {f"t{i}": [j < solved for j in range(N)] for i, solved in enumerate([0, 1, 2, 3] * 75)}


def two_stage_reference(results: dict[str, list[bool]]) -> tuple[float, float]:
    """The 95% interval the two-stage bootstrap converges to, in closed form.

    One resampled task contributes Binomial(n, p_t) / n for a uniformly drawn t, whose
    variance is the spread of the p_t (with 1/T) plus the mean binomial variance
    p_t (1 - p_t) / n. The mean of T such draws is close to normal for large T.
    """
    rates = [sum(t) / len(t) for t in results.values()]
    t = len(rates)
    mean = sum(rates) / t
    between = sum((p - mean) ** 2 for p in rates) / t
    within = sum(p * (1 - p) / N for p in rates) / t
    half = 1.959964 * math.sqrt((between + within) / t)
    return mean - half, mean + half


def test_solve_rate_is_the_mean_of_per_task_rates() -> None:
    assert solve_rate({"a": [True, True, False], "b": [False]}) == pytest.approx(1 / 3)


def test_bootstrap_interval_matches_the_closed_form_reference() -> None:
    low, high = bootstrap_ci(RESULTS)
    ref_low, ref_high = two_stage_reference(RESULTS)
    assert low == pytest.approx(ref_low, abs=0.01)
    assert high == pytest.approx(ref_high, abs=0.01)
    assert (ref_low, ref_high) == pytest.approx((0.4525, 0.5475), abs=0.001)


def test_bootstrap_of_a_perfect_score_has_no_width() -> None:
    assert bootstrap_ci({"a": [True] * 3, "b": [True] * 3}, resamples=200) == (1.0, 1.0)


def test_paired_difference_of_identical_results_keeps_only_the_trial_noise() -> None:
    """Pairing removes the spread between tasks: what is left is the binomial noise
    of both sides, 2 * mean(p (1 - p) / n) / T."""
    diff, low, high, tasks = paired_difference(RESULTS, RESULTS)
    rates = [sum(t) / N for t in RESULTS.values()]
    half = 1.959964 * math.sqrt(2 * sum(p * (1 - p) / N for p in rates) / len(rates) ** 2)
    assert (diff, tasks) == (0, 300)
    assert low == pytest.approx(-half, abs=0.01)
    assert high == pytest.approx(half, abs=0.01)


def test_paired_difference_uses_only_shared_tasks() -> None:
    a = {"x": [True] * 3, "y": [True] * 3}
    b = {"x": [False] * 3, "z": [True] * 3}
    assert paired_difference(a, b, resamples=200) == (1.0, 1.0, 1.0, 1)


@pytest.mark.parametrize(
    ("n", "c", "at_k", "hat_k"),
    [(3, 0, 0.0, 0.0), (3, 1, 1.0, 0.0), (3, 3, 1.0, 1.0), (5, 2, 0.9, 0.0), (5, 4, 1.0, 0.4)],
)
def test_pass_at_k_and_pass_hat_k(n: int, c: int, at_k: float, hat_k: float) -> None:
    assert pass_at_k(n, c, 3) == pytest.approx(at_k)
    assert pass_hat_k(n, c, 3) == pytest.approx(hat_k)


def test_tasks_with_fewer_than_k_trials_are_left_out() -> None:
    results = {"a": [True, False, False], "b": [True]}
    assert mean_over_tasks(results, 3, "pass@k") == 1.0
    assert mean_over_tasks({"b": [True]}, 3, "pass^k") is None
