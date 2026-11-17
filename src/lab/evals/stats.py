"""Eval statistics over repeated trials per task.

Repeats of one task are not independent samples, so the solve rate is the mean
of per-task solve rates, and its interval comes from a two-stage bootstrap:
resample the tasks with replacement, then each chosen task's trials with
replacement. A pooled Wilson interval over all trials would be too narrow.
"""

import math
import random
from collections.abc import Sequence

Results = dict[str, list[bool]]  # task -> the outcome of each trial
RESAMPLES = 10_000


def solve_rate(results: Results) -> float:
    return sum(sum(t) / len(t) for t in results.values()) / len(results)


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (0..100), interpolating linearly between closest ranks."""
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low = math.floor(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _trials(outcomes: list[bool]) -> tuple[int, float]:
    return len(outcomes), sum(outcomes) / len(outcomes)


def _resampled_rate(rng: random.Random, n: int, rate: float) -> float:
    return rng.binomialvariate(n, rate) / n


def bootstrap_ci(
    results: Results, resamples: int = RESAMPLES, seed: int = 0, level: float = 0.95
) -> tuple[float, float]:
    """Two-stage bootstrap percentile interval of `solve_rate`."""
    rng = random.Random(seed)
    tasks = [_trials(t) for t in results.values()]
    stats = []
    for _ in range(resamples):
        chosen = rng.choices(tasks, k=len(tasks))
        stats.append(sum(_resampled_rate(rng, n, p) for n, p in chosen) / len(chosen))
    tail = (1 - level) / 2 * 100
    return percentile(stats, tail), percentile(stats, 100 - tail)


def paired_difference(
    a: Results, b: Results, resamples: int = RESAMPLES, seed: int = 0, level: float = 0.95
) -> tuple[float, float, float, int]:
    """Mean per-task difference in solve rate (a - b) over the tasks both have, with
    its two-stage bootstrap interval: the same tasks are drawn for both sides, the
    trials of each side are resampled on their own. Returns (diff, low, high, tasks)."""
    shared = sorted(set(a) & set(b))
    pairs = [(_trials(a[t]), _trials(b[t])) for t in shared]
    diff = sum(pa - pb for (_, pa), (_, pb) in pairs) / len(pairs)
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        chosen = rng.choices(pairs, k=len(pairs))
        stats.append(
            sum(_resampled_rate(rng, *x) - _resampled_rate(rng, *y) for x, y in chosen)
            / len(chosen)
        )
    tail = (1 - level) / 2 * 100
    return diff, percentile(stats, tail), percentile(stats, 100 - tail), len(pairs)


def pass_at_k(n: int, c: int, k: int) -> float:
    """Chance that at least one of k trials solves the task (unbiased estimator from
    n trials with c successes, as in the Codex paper)."""
    if n - c < k:
        return 1.0
    return 1 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Chance that all of k trials solve the task (pass^k, from n trials with c successes)."""
    return math.comb(c, k) / math.comb(n, k)


def mean_over_tasks(results: Results, k: int, metric: str) -> float | None:
    """pass@k or pass^k averaged over the tasks with at least k trials."""
    fn = pass_at_k if metric == "pass@k" else pass_hat_k
    values = [fn(len(t), sum(t), k) for t in results.values() if len(t) >= k]
    return sum(values) / len(values) if values else None
