"""One microsandbox VM per session: create, reattach or fork it, run the in-VM runner.

VMs are created detached, so they outlive the API process (a crash of the API is
a crash of the VM owner). The VM name is derived from the session id and recorded
in the log with `sandbox_provisioning` and `sandbox_attached`.

The repo is cloned (or, for a local fixture, archived) on the host and copied in,
because a VM for a host-placement harness has no network at all.
"""

import asyncio
import contextlib
import io
import json
import os
import tarfile
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from microsandbox import Job, MicrosandboxError, Network, Sandbox, SandboxStatus

from lab import config
from lab.events import AgentEvent

IMAGE = "python:3.13"  # full image: git included (see the plan's base image decision)
REPO_DIR = "/lab/repo"
RUNS_DIR = "/lab/runs"
RUNNER_GUEST = "/lab/runner.py"
RUNNER_HOST = Path(__file__).parent / "runner" / "runner.py"
ARCHIVE_GUEST = "/lab/repo.tar.gz"
BASE_GUEST = "/lab/base"  # the starting commit's sha
PATCH_GUEST = "/lab/workspace.patch"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"  # the only local repos allowed
STDERR_LIMIT = 4000


class SandboxError(Exception):
    """Provisioning the VM, or the runner inside it, failed."""


def sandbox_name(session_id: UUID) -> str:
    return f"lab-{session_id}"


def _ensure_home() -> None:
    """Workaround for microsandbox issue #2041 (ENOENT on a fresh home)."""
    (Path.home() / ".microsandbox" / "sandboxes").mkdir(parents=True, exist_ok=True)


async def _check(sb: Sandbox, script: str, timeout: float = 300) -> None:
    out = await sb.shell(script, timeout=timeout)
    if out.exit_code != 0:
        raise SandboxError(f"{script!r} exited {out.exit_code}: {out.stderr_text[-STDERR_LIMIT:]}")


def _tar(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(directory, arcname=".")
    return buffer.getvalue()


async def _git(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        detail = err.decode(errors="replace")[-STDERR_LIMIT:]
        raise SandboxError(f"git {args[0]} exited {proc.returncode}: {detail}")


async def _repo_archive(repo_url: str, commit: str | None) -> tuple[bytes, str | None]:
    """A gzipped tar of the repo, built on the host, and the commit it was taken at.

    `repo_url` is an https git URL (cloned into a temp dir, at `commit` if given)
    or a directory under `fixtures/` (no commit). Other schemes are refused: the
    clone runs on the host, where `file://` or `ssh://` would reach the host's own
    repos and credentials.
    """
    local = Path(repo_url).expanduser()
    if local.is_dir():
        local = local.resolve()
        if not local.is_relative_to(FIXTURES):
            raise SandboxError(f"local repos must live under {FIXTURES}")
        return await asyncio.to_thread(_tar, local), None
    if not repo_url.startswith("https://"):
        raise SandboxError("repo_url must be an https:// git URL or a directory under fixtures/")
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "repo"
        await _git("clone", "-q", "--", repo_url, str(clone))
        if commit:  # a VM recreated later must start from the same code
            await _git("-C", str(clone), "checkout", "-q", commit)
        else:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(clone), "rev-parse", "HEAD", stdout=asyncio.subprocess.PIPE
            )
            out, _ = await proc.communicate()
            commit = out.decode().strip()
        return await asyncio.to_thread(_tar, clone), commit


# A directory without git history (a fixture) becomes a single-commit repo, so the
# agent can `git diff` its own changes. The starting commit is kept outside the
# repo, as the base of the workspace snapshots.
_UNPACK = (
    f"mkdir -p {REPO_DIR} {RUNS_DIR}"
    f" && tar -xzf {ARCHIVE_GUEST} -C {REPO_DIR} --no-same-owner && rm {ARCHIVE_GUEST}"
    f" && cd {REPO_DIR} && {{ test -d .git || {{ git init -q -b main && git add -A"
    " && git -c user.name=lab -c user.email=lab@localhost commit -q -m initial; }; }"
    f" && git rev-parse HEAD > {BASE_GUEST}"
)

# The working tree against the starting commit, untracked files included, as one
# binary patch. A throwaway index keeps the agent's own index and commits untouched.
_SNAPSHOT = (
    f"cd {REPO_DIR} && export GIT_INDEX_FILE=/lab/snapshot.index"
    f' && git read-tree "$(cat {BASE_GUEST})" && git add -A'
    f' && git diff --cached --binary "$(cat {BASE_GUEST})" > {PATCH_GUEST}'
    "; status=$?; rm -f /lab/snapshot.index; exit $status"
)


async def attach(name: str) -> Sandbox | None:
    """Connect to a running VM by name; None if it does not exist or is not running."""
    try:
        handle = await Sandbox.get(name)
    except MicrosandboxError:
        return None
    if handle.status != SandboxStatus.RUNNING:
        return None
    return await handle.connect()


async def provision(
    name: str,
    repo_url: str,
    placement: Literal["host", "sandbox"],
    commit: str | None = None,
    workspace: bytes | None = None,
) -> tuple[Sandbox, str | None]:
    """Create the VM `name` (replacing any old one) with a copy of the repo.

    Returns the VM and the commit of an https clone. With `workspace` (a patch from
    `save_workspace`) the repo is put back into that state, which is how a lost VM
    or a fork that cannot use `fork()` is rebuilt.

    A VM for a `host` harness only runs tools, so it gets no network at all. A
    `sandbox` harness runs its agent inside and keeps the default (public) network.
    """
    _ensure_home()
    archive, commit = await _repo_archive(repo_url, commit)
    sb = await Sandbox.create(
        name,
        image=IMAGE,
        cpus=config.VM_CPUS,
        memory=config.VM_MEMORY_MIB,
        network=Network.none() if placement == "host" else None,
        detached=True,
        replace=True,
    )
    # detach() consumes this handle; reconnecting gives one that does not own the VM.
    await sb.detach()
    sb = await (await Sandbox.get(name)).connect()
    try:
        await _check(sb, "mkdir -p /lab")
        await sb.fs.write(ARCHIVE_GUEST, archive)
        await _check(sb, _UNPACK)
        await sb.fs.copy_from_host(str(RUNNER_HOST), RUNNER_GUEST)
        if workspace:
            await sb.fs.write(PATCH_GUEST, workspace)
            await _check(sb, f"cd {REPO_DIR} && git apply --whitespace=nowarn {PATCH_GUEST}")
    except Exception:
        await sb.destroy(force=True)  # a VM without its repo must not be reattached later
        raise
    return sb, commit


async def fork(source: Sandbox, name: str) -> Sandbox:
    """Copy-on-write fork of a running VM (memory and disk), detached like the source.

    microsandbox refuses to fork a VM that uses host-backed network resources, which
    includes the `Network.none()` policy of host-placement VMs.
    """
    sb = await source.fork(name)
    if await sb.owns_lifecycle:
        await sb.detach()
        sb = await (await Sandbox.get(name)).connect()
    return sb


async def save_workspace(sb: Sandbox) -> bytes:
    """The VM's working tree as a patch against the starting commit (see `_SNAPSHOT`)."""
    await _check(sb, _SNAPSHOT)
    return await sb.fs.read(PATCH_GUEST)


def _event(line: bytes) -> AgentEvent:
    data = json.loads(line)
    return AgentEvent(data["type"], data["payload"], data["run_id"], data.get("rseq"))


async def job_events(job: Job, stderr: bytearray | None = None) -> AsyncIterator[AgentEvent]:
    """The runner's JSONL events from the job's stdout, from the start, until it exits."""
    buffer = b""
    async for entry in await job.follow_logs():
        if entry.source == "stdout":
            buffer += entry.data
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if line.strip():
                    yield _event(line)
        elif entry.source == "stderr" and stderr is not None:
            stderr += entry.data
            del stderr[:-STDERR_LIMIT]


async def exec_runner(sb: Sandbox, task: dict[str, Any]) -> AsyncIterator[AgentEvent]:
    """Run `runner.py` in the VM with `task` on stdin and yield its JSONL events.

    The runner is a managed job (`exec_detached`), not an `exec_stream`: a process
    started with `exec_stream` dies with the host process that started it, a job
    keeps running if the API dies, and its output can be followed again later.
    The run id is also on the command line, so `runner_job` can find the job.
    """
    stdin = json.dumps({**task, "runs_dir": RUNS_DIR}).encode()
    job = await sb.exec_detached(
        "python3", [RUNNER_GUEST, task["run_id"]], cwd=REPO_DIR, stdin=stdin
    )
    stderr = bytearray()
    done = False
    try:
        async for event in job_events(job, stderr):
            yield event
        done = True
    finally:
        if not done:  # the run was cancelled (timeout, shutdown): stop the agent too
            with contextlib.suppress(MicrosandboxError):
                await job.kill()
    result = await job.wait()
    if result.code != 0:
        raise SandboxError(f"runner exited {result.code}: {stderr.decode(errors='replace')}")


async def runner_job(sb: Sandbox, run_id: str) -> Job | None:
    """The runner job of `run_id` if it is still running in the VM."""
    cursor = None
    while True:
        page = await sb.list_jobs(all=True, cursor=cursor)
        for info in page.items:
            if run_id in info.command and info.state in ("starting", "running"):
                return await sb.get_job(info.id)
        if page.next_cursor is None:
            return None
        cursor = page.next_cursor


async def run_file_events(sb: Sandbox, run_id: str) -> AsyncIterator[AgentEvent]:
    """The durable events the runner wrote to `runs/<run_id>.jsonl` so far.

    A last line without its newline is still being written and is skipped.
    """
    try:
        data = await sb.fs.read(f"{RUNS_DIR}/{run_id}.jsonl")
    except MicrosandboxError:
        return  # the runner never started
    *lines, _partial = data.split(b"\n")
    for line in lines:
        if line.strip():
            yield _event(line)


async def destroy(session_id: UUID) -> None:
    try:
        handle = await Sandbox.get(sandbox_name(session_id))
    except MicrosandboxError:
        return
    await handle.destroy(force=True)
