"""The grader's adversarial suite, run against every task before any real eval.

    python -m lab.evals.check [task ...]

For each task:
- the reference diff passes;
- an empty diff fails, with exactly the `fail_to_pass` tests failing (so the task
  is valid: those tests fail at the starting commit, the others pass);
- a diff that edits a hidden test path is rejected;
- a diff that adds a `conftest.py` forcing every test to pass is rejected;
- a diff that hardcodes the examples of the instruction fails;
- a run that collects zero tests fails.
Exits 1 if any check does not hold.
"""

import asyncio
import dataclasses
import sys

from lab.evals.grader import Verdict, grade
from lab.evals.tasks import Task, load_all

CONCURRENT_VMS = 4
ATTEMPTS = 3

FORCE_PASS_CONFTEST = """import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    outcome.get_result().outcome = "passed"
"""


def new_file_diff(path: str, content: str) -> str:
    lines = content.splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}"
    )


async def _grade(slots: asyncio.Semaphore, task: Task, diff: str) -> Verdict:
    async with slots:
        for _ in range(ATTEMPTS):
            verdict = await grade(task, diff.encode())
            if verdict.status != "infra_error":
                break
        return verdict


async def check(task: Task, slots: asyncio.Semaphore) -> list[str]:
    """The checks that did not hold for `task`, as messages."""
    hidden = sorted(task.hidden_paths())[0]
    no_tests = dataclasses.replace(task, tests={p: b"# no tests\n" for p in task.tests})
    reference, empty, edit_hidden, conftest, hardcode, zero = await asyncio.gather(
        _grade(slots, task, task.solution),
        _grade(slots, task, ""),
        _grade(slots, task, new_file_diff(hidden, "def test_ok():\n    assert True\n")),
        _grade(slots, task, new_file_diff("conftest.py", FORCE_PASS_CONFTEST)),
        _grade(slots, task, task.hardcode),
        _grade(slots, no_tests, task.solution),
    )
    problems = []
    if not reference.passed:
        problems.append(f"reference diff: {reference.status} {reference.reason} {reference.log}")
    if empty.status != "fail" or set(empty.failed) != set(task.fail_to_pass):
        problems.append(f"empty diff: {empty.status} {empty.reason}, failed {empty.failed}")
    if edit_hidden.status != "rejected":
        problems.append(f"diff editing {hidden}: {edit_hidden.status} {edit_hidden.reason}")
    if conftest.status != "rejected":
        problems.append(f"conftest.py diff: {conftest.status} {conftest.reason}")
    if hardcode.status != "fail":
        problems.append(f"hardcoded diff: {hardcode.status} {hardcode.reason}")
    if (zero.status, zero.reason) != ("fail", "no_tests_collected"):
        problems.append(f"zero collected tests: {zero.status} {zero.reason}")
    return problems


async def main(names: list[str]) -> int:
    tasks = load_all(names or None)
    slots = asyncio.Semaphore(CONCURRENT_VMS)
    results = await asyncio.gather(*(check(t, slots) for t in tasks))
    for task, problems in zip(tasks, results, strict=True):
        print(f"{task.name}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"  {problem}")
    failed = sum(bool(p) for p in results)
    print(f"eval-check: {len(tasks) - failed} of {len(tasks)} tasks ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
