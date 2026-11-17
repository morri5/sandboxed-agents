"""The eval tasks under `evals/tasks/<name>/`, in a Harbor-compatible layout.

    instruction.md   the prompt, sent to the agent as its one message
    environment/     the repo at the starting commit (no `.git`): all the agent's VM gets
    tests/           hidden tests, plus `config.json` with `fail_to_pass` and `pass_to_pass`
    solution/        `solution.diff`, the reference diff
    adversarial/     `hardcode.diff`, a diff that special-cases the visible examples

`tests/`, `solution/` and `adversarial/` never reach an agent's VM: a session can
only be created on `environment/` (see `sandbox._repo_archive`).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lab.sandbox import EVAL_TASKS


@dataclass(frozen=True)
class Task:
    name: str
    instruction: str
    environment: Path
    tests: dict[str, bytes]  # path under `tests/` (e.g. "tests/test_x.py") -> content
    fail_to_pass: tuple[str, ...]  # pytest node ids, relative to the grader's root
    pass_to_pass: tuple[str, ...]
    solution: str  # the reference diff
    hardcode: str  # the adversarial diff

    @property
    def test_ids(self) -> tuple[str, ...]:
        return self.fail_to_pass + self.pass_to_pass

    def hidden_paths(self) -> set[str]:
        """Repo paths of the hidden tests: test files the agent's repo does not have."""
        return {path for path in self.tests if not (self.environment / path).exists()}

    def checksum(self) -> str:
        """sha256 over the hidden tests, as `sha256sum` prints them, one line per file."""
        return "".join(
            f"{hashlib.sha256(content).hexdigest()}  {path}\n"
            for path, content in sorted(self.tests.items())
        )


def load(directory: Path) -> Task:
    config = json.loads((directory / "tests" / "config.json").read_text())
    environment = directory / "environment"
    if (environment / ".git").exists():
        raise ValueError(f"{environment} must not be a git repo (history would leak)")
    tests = {
        f"tests/{path.name}": path.read_bytes()
        for path in sorted((directory / "tests").glob("test_*.py"))
    }
    task = Task(
        name=directory.name,
        instruction=(directory / "instruction.md").read_text(),
        environment=environment,
        tests=tests,
        fail_to_pass=tuple(config["fail_to_pass"]),
        pass_to_pass=tuple(config["pass_to_pass"]),
        solution=(directory / "solution" / "solution.diff").read_text(),
        hardcode=(directory / "adversarial" / "hardcode.diff").read_text(),
    )
    if not task.fail_to_pass:
        raise ValueError(f"{task.name}: no fail_to_pass tests")
    unknown = {i.split("::")[0] for i in task.test_ids} - set(tests)
    if unknown:
        raise ValueError(f"{task.name}: test ids in files that are not in tests/: {unknown}")
    return task


def load_all(names: list[str] | None = None, root: Path = EVAL_TASKS) -> list[Task]:
    """Every task, or the named ones, sorted by name."""
    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    if names:
        missing = set(names) - {d.name for d in dirs}
        if missing:
            raise ValueError(f"unknown tasks: {sorted(missing)}")
        dirs = [d for d in dirs if d.name in names]
    return [load(d) for d in dirs]
