"""The grader's rules that need no VM: which diffs are rejected, and pass or fail
from pytest's results. The VM part runs in `make eval-check`."""

import dataclasses
from pathlib import Path

import pytest

from lab.evals.check import FORCE_PASS_CONFTEST, new_file_diff
from lab.evals.grader import parse_diff, verdict_from, violations
from lab.evals.tasks import Task, load_all, tier
from lab.sandbox import EVAL_TASKS, SandboxError, _repo_archive

TASKS = load_all()


@pytest.fixture
def task() -> Task:
    return next(t for t in TASKS if t.name == "slugify-separators")


def test_suite_size_and_layout() -> None:
    assert 8 <= sum(tier(t.name) == "easy" for t in TASKS) <= 12
    assert sum(tier(t.name) == "hard" for t in TASKS) == 5
    for t in TASKS:
        assert t.hidden_paths(), t.name  # every task has tests the agent never sees
        assert not (t.environment / ".git").exists()


@pytest.mark.parametrize("t", TASKS, ids=lambda t: t.name)
def test_reference_and_hardcoded_diffs_are_gradable(t: Task) -> None:
    assert violations(t.solution, t) == []
    assert violations(t.hardcode, t) == []


def test_editing_a_hidden_test_is_rejected(task: Task) -> None:
    found = violations(new_file_diff("tests/test_hidden.py", "def test_ok():\n    pass\n"), task)
    assert found == ["hidden test path: tests/test_hidden.py"]


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py",
        "tests/conftest.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "sitecustomize.py",
        "pkg/usercustomize.py",
        "evil.pth",
        ".github/workflows/ci.yml",
        "uv.lock",
        "../outside.py",
    ],
)
def test_protected_paths_are_rejected(task: Task, path: str) -> None:
    assert violations(new_file_diff(path, FORCE_PASS_CONFTEST), task)


def test_a_new_ordinary_file_is_fine(task: Task) -> None:
    assert violations(new_file_diff("textkit/util.py", "X = 1\n"), task) == []


PYPROJECT = """diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -3,3 +3,6 @@ name = "textkit"
 version = "0.1.0"
 description = "Small text helpers."
 requires-python = ">=3.11"
+
+[{table}]
+{line}
"""


def test_pytest_table_in_pyproject_is_rejected(task: Task) -> None:
    diff = PYPROJECT.format(table="tool.pytest.ini_options", line='addopts = "-p no:x"')
    assert violations(diff, task) == ["pytest configuration in pyproject.toml"]


def test_other_pyproject_tables_are_fine(task: Task) -> None:
    diff = PYPROJECT.format(table="tool.ruff", line="line-length = 100")
    assert violations(diff, task) == []


def test_editing_inside_an_existing_pytest_table_is_rejected(task: Task, tmp_path: Path) -> None:
    original = '[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = ""\n'
    (tmp_path / "pyproject.toml").write_text(original)
    diff = (
        "diff --git a/pyproject.toml b/pyproject.toml\n--- a/pyproject.toml\n"
        '+++ b/pyproject.toml\n@@ -5 +5 @@\n-addopts = ""\n+addopts = "-x"\n'
    )
    found = violations(diff, dataclasses.replace(task, environment=tmp_path))
    assert found == ["pytest configuration in pyproject.toml"]


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ("old mode 100644\nnew mode 100755\n", "mode change"),
        ("new file mode 120000\n", "special file"),
        ("new file mode 100755\n", "special file"),
        ("deleted file mode 120000\n", "special file"),
        ("new file mode 100644\nindex 0000000..e69de29\nGIT binary patch\nliteral 0\n", "binary"),
    ],
)
def test_binary_files_symlinks_and_mode_changes_are_rejected(
    task: Task, headers: str, reason: str
) -> None:
    diff = f"diff --git a/textkit/x b/textkit/x\n{headers}"
    found = violations(diff, task)
    assert found and all(f.startswith(reason) for f in found), found


@pytest.mark.parametrize(
    "diff",
    [
        "not a diff\n",
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\n",  # truncated
        'diff --git "a/x\\ty.py" "b/x\\ty.py"\n--- "a/x\\ty.py"\n+++ "b/x\\ty.py"\n',  # quoted
    ],
)
def test_unparseable_diffs_are_rejected(task: Task, diff: str) -> None:
    assert violations(diff, task)[0].startswith("unparseable diff")


def test_a_removed_line_that_looks_like_a_header_is_hunk_content() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,1 @@\n"
        "--- conftest.py\n x = 1\n"
    )
    (f,) = parse_diff(diff)
    assert f.paths == {"a.py"}
    assert f.hunks == [(1, ["--- conftest.py", " x = 1"])]


def test_renames_count_both_paths(task: Task) -> None:
    diff = (
        "diff --git a/textkit/__init__.py b/conftest.py\nsimilarity index 100%\n"
        "rename from textkit/__init__.py\nrename to conftest.py\n"
    )
    assert violations(diff, task) == ["protected path: conftest.py"]


def test_verdict(task: Task) -> None:
    passed = dict.fromkeys(task.test_ids, "passed")
    assert verdict_from(task, 0, len(passed), passed).status == "pass"
    assert verdict_from(task, 5, 0, {}).reason == "no_tests_collected"
    one_failed = {**passed, task.fail_to_pass[0]: "failed"}
    verdict = verdict_from(task, 1, len(passed), one_failed)
    assert (verdict.status, verdict.failed) == ("fail", (task.fail_to_pass[0],))
    missing = {k: v for k, v in passed.items() if k != task.pass_to_pass[0]}
    assert verdict_from(task, 0, len(missing), missing).failed == (task.pass_to_pass[0],)
    assert verdict_from(task, 1, len(passed), passed).reason == "pytest_exit_1"


async def test_only_the_environment_of_a_task_can_become_a_repo(task: Task) -> None:
    archive, commit = await _repo_archive(str(task.environment), None)
    assert archive and commit is None
    for path in (EVAL_TASKS / task.name, EVAL_TASKS / task.name / "tests", EVAL_TASKS):
        with pytest.raises(SandboxError):
            await _repo_archive(str(path), None)
