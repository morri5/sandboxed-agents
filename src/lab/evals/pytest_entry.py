"""Runs inside the grader VM: pytest with a recorder of each test's outcome.

    python3 -I pytest_entry.py <pytest deps dir> <repo dir> <results file> <pytest args...>

`-I` keeps the current directory, `PYTHON*` variables and user site-packages off
`sys.path`. The pytest install goes first and the repo last, so a module in the
agent's diff cannot shadow pytest or the standard library. The outcome of every
test (by node id) and the number collected are written as JSON to the results
file. Standard library and pytest only.
"""

import json
import sys
from typing import Any


class Recorder:
    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {}
        self.collected = 0
        self.collection_errors = 0

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors += 1

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        """A test passed only if setup, call and teardown all passed."""
        previous = self.outcomes.get(report.nodeid)
        if report.failed:
            self.outcomes[report.nodeid] = "failed"
        elif report.skipped and previous != "failed":
            self.outcomes[report.nodeid] = "skipped"
        elif report.when == "call" and previous is None:
            self.outcomes[report.nodeid] = "passed"


def main() -> None:
    deps, repo, results, *args = sys.argv[1:]
    sys.path.insert(0, deps)
    sys.path.append(repo)
    import pytest

    recorder = Recorder()
    code = pytest.main(args, plugins=[recorder])
    with open(results, "w") as out:
        json.dump(
            {
                "exit_code": int(code),
                "collected": recorder.collected,
                "collection_errors": recorder.collection_errors,
                "outcomes": recorder.outcomes,
            },
            out,
        )


if __name__ == "__main__":
    main()
