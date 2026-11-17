"""Generate the hard tier's repos from the shared `evals/repos/ledger/` app.

    uv run python scripts/build_hard_tasks.py          # write the generated files
    uv run python scripts/build_hard_tasks.py --check  # exit 1 if a committed one differs

For every `evals/tasks/hard-*/`:
- `environment/` is the ledger app plus the task's `start.diff` (the bug it
  plants; feature tasks have none);
- the visible test files in `tests/` (every `test_*.py` but `test_hidden.py`),
  which the grader runs as pristine copies, are taken from the environment with
  the reference solution applied. Usually that is the environment's own copy; a
  task whose fix has to change a visible test (hard-rename-field) gets the
  updated one.

Everything else in a task directory is written by hand. The generated files are
committed, so the Harbor layout and the grader stay the same as for the easy tier.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "evals" / "repos" / "ledger"
TASKS = ROOT / "evals" / "tasks"
HIDDEN = "test_hidden.py"
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")


def _apply(diff: Path, directory: Path) -> None:
    if diff.is_file() and diff.read_text().strip():
        # `directory` is a temp dir outside any git repo, so paths are relative to it.
        subprocess.run(["git", "apply", str(diff)], cwd=directory, check=True)


def build(task: Path, out: Path) -> None:
    """Write the task's generated files under `out` (`environment/` and `tests/`)."""
    env = out / "environment"
    shutil.copytree(LEDGER, env, ignore=IGNORE)
    _apply(task / "start.diff", env)
    with tempfile.TemporaryDirectory() as tmp:
        solved = Path(tmp) / "solved"
        shutil.copytree(env, solved)
        _apply(task / "solution" / "solution.diff", solved)
        (out / "tests").mkdir()
        for test in sorted((solved / "tests").glob("test_*.py")):
            shutil.copy2(test, out / "tests" / test.name)


def _files(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def _generated_tests(task: Path) -> dict[str, bytes]:
    tests = sorted((task / "tests").glob("test_*.py"))
    return {p.name: p.read_bytes() for p in tests if p.name != HIDDEN}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args()
    tasks = sorted(TASKS.glob("hard-*"))
    stale = []
    for task in tasks:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build(task, out)
            env, tests = _files(out / "environment"), _files(out / "tests")
            if HIDDEN in tests:
                raise SystemExit(f"{task.name}: the ledger must not have tests/{HIDDEN}")
            if args.check:
                same = _files(task / "environment") == env and _generated_tests(task) == tests
                print(f"{task.name}: {'up to date' if same else 'OUT OF DATE'}")
                if not same:
                    stale.append(task.name)
                continue
            shutil.rmtree(task / "environment", ignore_errors=True)
            shutil.copytree(out / "environment", task / "environment")
            for name in _generated_tests(task):
                (task / "tests" / name).unlink()
            for name in tests:
                shutil.copy2(out / "tests" / name, task / "tests" / name)
        print(f"{task.name}: written")
    if stale:
        print(f"out of date (run scripts/build_hard_tasks.py): {', '.join(stale)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
