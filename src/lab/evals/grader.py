"""The grader: hidden tests on the agent's final diff, in a fresh VM the agent never touched.

1. Only the diff counts: the patch the session saved after the run (`workspace_saved`).
2. It is rejected if it touches a protected path (hidden tests, pytest and Python
   start-up hooks, CI config, lockfiles, the `[tool.pytest]` table of a
   `pyproject.toml`), or contains a binary file, a symlink or a mode change.
3. Otherwise it is applied in a fresh VM built from the task's `environment/`,
   with no network and pytest mounted read-only from the host.
4. The hidden tests are copied to `/grader`, outside the repo, and checked
   against their sha256; pytest runs with the grader's config and root, so no
   file from the repo can configure it.
5. Pass means every `fail_to_pass` and `pass_to_pass` test passed and pytest
   exited 0. No collected test, a crash or unreadable results are a fail.
6. A VM that cannot be created is an `infra_error`, not a fail.

Threat model: the diff cannot reach the tests or pytest's configuration, but the
agent's code does run inside the pytest process (it is what is being tested), so
code written to attack the grader could forge results. That is what reading the
transcripts is for.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from microsandbox import ExecTimeoutError, MicrosandboxError, Network, Volume

from lab.evals.tasks import Task
from lab.sandbox import REPO_DIR, VmSpec, provision

PYTEST_VERSION = "9.2.0"
PYTEST_HOST = Path(__file__).resolve().parents[3] / ".cache" / f"pytest-{PYTEST_VERSION}"
PYTEST_GUEST = "/opt/lab-pytest"
GRADER_DIR = "/grader"
ENTRY_HOST = Path(__file__).parent / "pytest_entry.py"
PATCH_GUEST = "/lab/agent.patch"
TEST_TIMEOUT_S = 120
LOG_LIMIT = 3000

PROTECTED_NAMES = {
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "sitecustomize.py",
    "usercustomize.py",
    # lockfiles
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    # CI
    ".gitlab-ci.yml",
    ".travis.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
}
PROTECTED_DIRS = {".github", ".circleci", ".buildkite"}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffError(ValueError):
    """The diff could not be parsed."""


@dataclass
class FileDiff:
    paths: set[str] = field(default_factory=set)  # old and new path, without a/ and b/
    headers: list[str] = field(default_factory=list)
    hunks: list[tuple[int, list[str]]] = field(default_factory=list)  # (old start, lines)


def _path(path: str, prefixed: bool) -> str | None:
    """A path from a header line; `---`/`+++` lines carry `a/` or `b/`, renames do not."""
    if path.startswith('"'):
        raise DiffError(f"quoted path {path}")  # git quotes unusual characters
    if path == "/dev/null":
        return None
    if prefixed:
        if path[:2] not in ("a/", "b/"):
            raise DiffError(f"path without a/ or b/: {path}")
        return path[2:]
    return path


def parse_diff(diff: str) -> list[FileDiff]:
    """The files of a `git diff`, with their extended headers and hunks."""
    lines = diff.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[FileDiff] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            raise DiffError(f"line {i + 1}: expected 'diff --git'")
        f = FileDiff()
        files.append(f)
        rest = lines[i][len("diff --git ") :]
        half = (len(rest) - 1) // 2  # "a/P b/P" when the path did not change
        if rest[:half][2:] == rest[half + 1 :][2:] and rest[half] == " ":
            f.paths.add(rest[2:half])
        i += 1
        while i < len(lines) and not lines[i].startswith(("diff --git ", "@@")):
            header = lines[i]
            f.headers.append(header)
            for prefix in ("--- ", "+++ ", "rename from ", "rename to ", "copy from ", "copy to "):
                if header.startswith(prefix):
                    path = _path(header[len(prefix) :], prefix in ("--- ", "+++ "))
                    if path:
                        f.paths.add(path)
            i += 1
        while i < len(lines) and lines[i].startswith("@@"):
            m = HUNK.match(lines[i])
            if not m:
                raise DiffError(f"line {i + 1}: bad hunk header")
            old_start = int(m.group(1))
            old_n = int(m.group(2) or 1)
            new_n = int(m.group(4) or 1)
            i += 1
            body: list[str] = []
            while old_n > 0 or new_n > 0 or (i < len(lines) and lines[i].startswith("\\")):
                if i >= len(lines):
                    raise DiffError("truncated hunk")
                line = lines[i]
                tag = line[:1]
                if tag in (" ", ""):
                    old_n, new_n = old_n - 1, new_n - 1
                elif tag == "-":
                    old_n -= 1
                elif tag == "+":
                    new_n -= 1
                elif tag != "\\":
                    raise DiffError(f"line {i + 1}: unexpected line in a hunk")
                body.append(line)
                i += 1
            f.hunks.append((old_start, body))
        if not f.paths:
            raise DiffError(f"no path for the diff before line {i + 1}")
    return files


def _section(header: str) -> str | None:
    text = header.strip()
    if text.startswith("[") and "]" in text:
        return text.strip("[]").strip()
    return None


def _touches_pytest_table(original: list[str], hunks: list[tuple[int, list[str]]]) -> bool:
    """Whether a changed line of a pyproject.toml is in (or opens) a `tool.pytest` table.

    The table in effect is tracked on both sides: removed lines in the old file,
    added lines in the new one.
    """
    for old_start, body in hunks:
        before = [s for line in original[: max(old_start - 1, 0)] if (s := _section(line))]
        old = new = before[-1] if before else ""
        for line in body:
            tag, text = line[:1], line[1:]
            section = _section(text)
            if section is not None:
                if tag in (" ", "", "-"):
                    old = section
                if tag in (" ", "", "+"):
                    new = section
            if tag == "-" and old.startswith("tool.pytest"):
                return True
            if tag == "+" and new.startswith("tool.pytest"):
                return True
    return False


def violations(diff: str, task: Task) -> list[str]:
    """Why the diff must be rejected; empty if it may be graded."""
    try:
        files = parse_diff(diff)
    except DiffError as exc:
        return [f"unparseable diff: {exc}"]
    hidden = task.hidden_paths()
    found: list[str] = []
    for f in files:
        for path in sorted(f.paths):
            p = PurePosixPath(path)
            if p.is_absolute() or ".." in p.parts:
                found.append(f"path outside the repo: {path}")
            elif path in hidden:
                found.append(f"hidden test path: {path}")
            elif p.name in PROTECTED_NAMES or p.suffix == ".pth" or PROTECTED_DIRS & set(p.parts):
                found.append(f"protected path: {path}")
            elif p.name == "pyproject.toml":
                original = task.environment / path
                lines = original.read_text().split("\n") if original.is_file() else []
                if _touches_pytest_table(lines, f.hunks):
                    found.append(f"pytest configuration in {path}")
        for header in f.headers:
            if header.startswith(("old mode", "new mode")):
                found.append(f"mode change: {header}")
            elif header.startswith("new file mode") and header.split()[-1] != "100644":
                found.append(f"special file ({header}): {sorted(f.paths)}")
            elif header.startswith("deleted file mode") and header.split()[-1] != "100644":
                found.append(f"special file ({header}): {sorted(f.paths)}")
            elif header.startswith(("GIT binary patch", "Binary files")):
                found.append(f"binary file: {sorted(f.paths)}")
    return found


@dataclass(frozen=True)
class Verdict:
    status: Literal["pass", "fail", "rejected", "infra_error"]
    reason: str
    failed: tuple[str, ...] = ()  # listed tests that did not pass
    seconds: float = 0.0
    log: str = ""  # the tail of pytest's output, or the error

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def spec() -> VmSpec:
    if not PYTEST_HOST.is_dir():
        raise RuntimeError(f"no pytest for the grader VM in {PYTEST_HOST}: run make grader-deps")
    return VmSpec(
        network=Network.none(),
        volumes={PYTEST_GUEST: Volume.bind(str(PYTEST_HOST), readonly=True)},
    )


async def grade(task: Task, patch: bytes) -> Verdict:
    """Grade the agent's final diff for `task` (see the module docstring)."""
    started = time.monotonic()
    found = violations(patch.decode(errors="replace"), task)
    if found:
        return Verdict("rejected", "; ".join(found))
    vm = spec()  # a missing pytest install is a setup error, not an infra error of this trial
    name = f"lab-grader-{uuid.uuid4().hex[:12]}"
    try:
        sb, _ = await provision(name, str(task.environment), vm)
    except Exception as exc:  # VM create or image pull: not the agent's fault
        return Verdict("infra_error", "vm_create", log=repr(exc)[-LOG_LIMIT:])
    try:
        verdict = await _run(sb, task, patch)
    except (MicrosandboxError, OSError) as exc:
        verdict = Verdict("infra_error", "vm_error", log=repr(exc)[-LOG_LIMIT:])
    finally:
        with contextlib.suppress(MicrosandboxError):
            await sb.destroy(force=True)
    return Verdict(
        verdict.status,
        verdict.reason,
        verdict.failed,
        round(time.monotonic() - started, 2),
        verdict.log,
    )


async def _run(sb: Any, task: Task, patch: bytes) -> Verdict:
    if patch.strip():
        await sb.fs.write(PATCH_GUEST, patch)
        out = await sb.shell(f"cd {REPO_DIR} && git apply --whitespace=nowarn {PATCH_GUEST}")
        if out.exit_code != 0:
            return Verdict("fail", "patch_does_not_apply", log=out.stderr_text[-LOG_LIMIT:])
    dirs = sorted({str(PurePosixPath(GRADER_DIR, p).parent) for p in task.tests})
    await sb.exec("mkdir", ["-p", *dirs])
    for path, content in task.tests.items():
        await sb.fs.write(f"{GRADER_DIR}/{path}", content)
    await sb.fs.write(f"{GRADER_DIR}/pytest.ini", b"[pytest]\n")
    await sb.fs.write(f"{GRADER_DIR}/pytest_entry.py", ENTRY_HOST.read_bytes())
    sums = await sb.exec("sha256sum", sorted(task.tests), cwd=GRADER_DIR)
    if sums.stdout_text != task.checksum():
        return Verdict("infra_error", "test_checksum_mismatch", log=sums.stdout_text)
    files = sorted({f"{GRADER_DIR}/{i.split('::')[0]}" for i in task.test_ids})
    results = f"{GRADER_DIR}/results.json"
    args = [
        "-I",
        f"{GRADER_DIR}/pytest_entry.py",
        PYTEST_GUEST,
        REPO_DIR,
        results,
        "-c",
        f"{GRADER_DIR}/pytest.ini",
        "--rootdir",
        GRADER_DIR,
        "-p",
        "no:cacheprovider",
        "--continue-on-collection-errors",
        "-q",
        *files,
    ]
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    try:
        out = await sb.exec("python3", args, cwd=REPO_DIR, env=env, timeout=TEST_TIMEOUT_S)
    except ExecTimeoutError:
        return Verdict("fail", "timeout")
    log = (out.stdout_text + out.stderr_text)[-LOG_LIMIT:]
    try:
        data = json.loads(await sb.fs.read(results))
        exit_code, collected = int(data["exit_code"]), int(data["collected"])
        outcomes: dict[str, str] = data["outcomes"]
    except (MicrosandboxError, ValueError, KeyError, TypeError):
        return Verdict("fail", "unreadable_results", log=log)
    return verdict_from(task, exit_code, collected, outcomes, log)


def verdict_from(
    task: Task, exit_code: int, collected: int, outcomes: dict[str, str], log: str = ""
) -> Verdict:
    if collected == 0:
        return Verdict("fail", "no_tests_collected", log=log)
    failed = tuple(i for i in task.test_ids if outcomes.get(i) != "passed")
    if failed:
        return Verdict("fail", "tests_failed", failed, log=log)
    if exit_code != 0:
        return Verdict("fail", f"pytest_exit_{exit_code}", log=log)
    return Verdict("pass", "all_tests_passed", log=log)


def install_pytest() -> None:
    """Install pytest for the grader VM (Linux arm64, Python 3.13) into `PYTEST_HOST`, once."""
    if PYTEST_HOST.is_dir():
        return
    tmp = PYTEST_HOST.with_name(f"{PYTEST_HOST.name}.tmp-{os.getpid()}")
    subprocess.run(
        ["uv", "pip", "install", "-q", "--target", str(tmp), "--python-version", "3.13",
         "--python-platform", "aarch64-manylinux_2_17", "--only-binary", ":all:",
         f"pytest=={PYTEST_VERSION}"],
        check=True,
    )  # fmt: skip
    try:
        tmp.rename(PYTEST_HOST)
    except OSError:  # another install finished first
        shutil.rmtree(tmp)
