"""The Markdown report of an eval results file (`python -m lab.evals.report <file.jsonl>`)."""

import json
import sys
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path
from typing import Any

from lab.evals import stats
from lab.evals.tasks import tier

K = 3  # pass@k and pass^k


def _label(group: tuple[str, str]) -> str:
    return f"`{group[0]}` ({group[1]})"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _usd(x: float) -> str:
    return f"${x:.4f}"


def _tiers(tasks: list[str]) -> list[tuple[str, set[str]]]:
    """Each tier present with its tasks, plus `all` when there is more than one."""
    tiers = [(t, {x for x in tasks if tier(x) == t}) for t in ("easy", "hard")]
    tiers = [(t, names) for t, names in tiers if names]
    return tiers + ([("all", set(tasks))] if len(tiers) > 1 else [])


def render(trials: list[dict[str, Any]]) -> str:
    graded = [t for t in trials if t["status"] != "infra_error"]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in graded:
        groups[(t["harness"], t["model"])].append(t)
    results: dict[tuple[str, str], stats.Results] = {}
    for group, ts in groups.items():
        per_task: stats.Results = defaultdict(list)
        for t in ts:
            per_task[t["task"]].append(t["passed"])
        results[group] = dict(per_task)
    tasks = sorted({t["task"] for t in graded})
    first = trials[0]
    lines = [
        f"# Eval report: {', '.join(sorted({t['eval_id'] for t in trials}))}",
        "",
        f"{len(trials)} trials on {len(tasks)} tasks. Every harness got the same VM"
        f" ({first['vm_cpus']} vCPU, {first['vm_memory_mib']} MiB), a {first['run_timeout_s']} s"
        f" run limit, a {first['exec_timeout_s']} s command limit and {first['max_tool_calls']}"
        " tool calls (the SDK's `max_turns` for the Claude harnesses).",
        "",
        "## Per task: solved trials",
        "",
        "| Task | Tier | " + " | ".join(_label(g) for g in results) + " |",
        "|---|---|" + "---|" * len(results),
    ]
    for task in tasks:
        cells = []
        for r in results.values():
            outcomes = r.get(task)
            cells.append(f"{sum(outcomes)}/{len(outcomes)}" if outcomes else "")
        lines.append(f"| {task} | {tier(task)} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Summary",
        "",
        "| Harness (model) | Tier | Trials | Solve rate | 95% CI | pass@3 | pass^3 | Rejected"
        " | Tokens p50 | Tokens p95 | USD total | USD per solved | Wall p50 | Wall p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    tiers = _tiers(tasks)
    for (group, all_trials), (tier_name, names) in product(groups.items(), tiers):
        ts = [t for t in all_trials if t["task"] in names]
        if not ts:
            continue
        r = {task: outcomes for task, outcomes in results[group].items() if task in names}
        low, high = stats.bootstrap_ci(r)
        totals = [float(sum(t["tokens"].values())) for t in ts if t["tokens"] is not None]
        no_usage = len(ts) - len(totals)
        tokens = (
            f"{stats.percentile(totals, 50):.0f} | {stats.percentile(totals, 95):.0f}"
            if totals
            else "n/a | n/a"
        ) + (f" ({no_usage} unknown)" if no_usage else "")
        walls = [float(t["wall_s"]) for t in ts]
        costs = [t["cost_usd"] for t in ts]
        spent = sum(c for c in costs if c is not None)
        unknown = sum(c is None for c in costs)
        solved = sum(t["passed"] for t in ts)
        per_solved = _usd(spent / solved) if solved else "n/a"
        total = _usd(spent) + (f" (+{unknown} unknown)" if unknown else "")
        rejected = sum(t["status"] == "rejected" for t in ts)
        lines.append(
            f"| {_label(group)} | {tier_name} | {len(ts)} | {_pct(stats.solve_rate(r))}"
            f" | {_pct(low)} to {_pct(high)}"
            f" | {_pct(stats.mean_over_tasks(r, K, 'pass@k'))}"
            f" | {_pct(stats.mean_over_tasks(r, K, 'pass^k'))} | {rejected}"
            f" | {tokens}"
            f" | {total} | {per_solved}"
            f" | {stats.percentile(walls, 50):.0f} s | {stats.percentile(walls, 95):.0f} s |"
        )
    pairs = list(combinations(results, 2))
    if pairs:
        lines += [
            "",
            "## Paired difference in solve rate (per task, same tasks on both sides)",
            "",
            "| A | B | Tier | Tasks | A minus B | 95% CI |",
            "|---|---|---|---|---|---|",
        ]
        for (a, b), (tier_name, names) in product(pairs, tiers):
            ra = {t: o for t, o in results[a].items() if t in names}
            rb = {t: o for t, o in results[b].items() if t in names}
            if not set(ra) & set(rb):
                continue
            diff, low, high, n = stats.paired_difference(ra, rb)
            lines.append(
                f"| {_label(a)} | {_label(b)} | {tier_name} | {n} | {100 * diff:+.0f} points"
                f" | {100 * low:+.0f} to {100 * high:+.0f} |"
            )
    infra = sum(len(t["infra_errors"]) for t in trials)
    lost = len(trials) - len(graded)
    priced = sorted({t["pricing_checked"] for t in graded if t.get("pricing_checked")})
    lines += [
        "",
        "## Notes",
        "",
        "- Tiers: `easy` tasks are one-function fixes in tiny packages; `hard` tasks (`hard-*`)"
        " are user reports against one shared app of about 20 modules, with no file or function"
        " named.",
        f"- {len(tasks)} tasks is below the 20 to 50 Anthropic suggests for an agent eval,"
        " so only large differences between harnesses are detectable; the intervals say how"
        " large.",
        "- Solve rate is the mean of per-task solve rates; its interval is a two-stage"
        f" bootstrap over tasks and then trials ({stats.RESAMPLES:,} resamples). pass@3 and"
        " pass^3 are the unbiased estimators over tasks with at least 3 trials.",
        "- Tokens are input plus output plus cache tokens per trial, as the harness reports them"
        " (`deepagents_ollama` reports no cache tokens). Wall time runs from `run_started` to the"
        " end of the run: VM creation and model latency included.",
        "- USD covers every graded trial, failed ones included."
        + (
            f" DeepSeek at peak prices checked {', '.join(priced)}."
            if priced
            else " Local models cost $0."
        )
        + " A run cut off by the run limit reports no usage: its tokens, and for a priced"
        " model its cost, count as unknown.",
        f"- Infrastructure errors: {infra} (re-run); trials lost to them: {lost}.",
        "",
        "## Trials",
        "",
        "Review a transcript with `uv run lab replay <session>` (with `make api` running).",
        "",
        "| Task | Harness | # | Status | Reason | Run end | Tool calls | Session |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in sorted(trials, key=lambda t: (t["harness"], t["task"], t["eval_id"], t["repeat"])):
        grade = t.get("grade", {})
        reason = grade.get("reason", "")
        if grade.get("failed"):
            reason += f" ({len(grade['failed'])} tests)"
        end = t.get("run_end", "") + (f" ({t['run_reason']})" if t.get("run_reason") else "")
        lines.append(
            f"| {t['task']} | `{t['harness']}` | {t['repeat']} | {t['status']} | {reason}"
            f" | {end} | {t.get('tool_calls', '')} | `{t['session_id']}` |"
        )
    return "\n".join(lines) + "\n"


def write(results: Path) -> Path:
    trials = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    path = results.with_suffix(".md")
    path.write_text(render(trials))
    return path


if __name__ == "__main__":
    print(write(Path(sys.argv[1])))
