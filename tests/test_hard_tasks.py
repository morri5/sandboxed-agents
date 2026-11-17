"""The hard tier: generated from the shared ledger app, selectable by pattern, its own tier."""

import subprocess
import sys
from pathlib import Path

import pytest

from lab.evals import report
from lab.evals.tasks import load_all

ROOT = Path(__file__).resolve().parents[1]


def test_committed_environments_match_the_build_script() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_hard_tasks.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_tasks_are_selected_by_pattern() -> None:
    names = [t.name for t in load_all(["hard-*"])]
    assert len(names) == 5
    assert all(n.startswith("hard-") for n in names)
    picked = load_all(["word-wrap", "hard-cent-*"])
    assert [t.name for t in picked] == ["hard-cent-drift", "word-wrap"]
    with pytest.raises(ValueError):
        load_all(["hard-nothing-*"])


def _trial(task: str, passed: bool) -> dict[str, object]:
    return {
        "eval_id": "e1",
        "vm_cpus": 1,
        "vm_memory_mib": 1024,
        "run_timeout_s": 900,
        "exec_timeout_s": 120,
        "max_tool_calls": 50,
        "task": task,
        "harness": "h",
        "model": "m",
        "repeat": 1,
        "session_id": "s",
        "infra_errors": [],
        "status": "pass" if passed else "fail",
        "passed": passed,
        "tokens": {"input": 1},
        "cost_usd": 0.0,
        "wall_s": 1.0,
    }


def test_report_shows_tiers_separately_and_combined() -> None:
    trials = [_trial("word-wrap", True), _trial("hard-cent-drift", False)]
    rows = [line for line in report.render(trials).splitlines() if line.startswith("| `h` (m) |")]
    assert [row.split(" | ")[1:4] for row in rows] == [
        ["easy", "1", "100%"],
        ["hard", "1", "0%"],
        ["all", "2", "50%"],
    ]
