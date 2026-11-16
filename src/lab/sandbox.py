"""One microsandbox VM per session: create or reattach, clone the repo, run the runner.

VMs are created detached, so they outlive the API process (a crash of the API is
a crash of the VM owner). The VM name is derived from the session id and recorded
in the log with `sandbox_attached`.
"""

import json
import shlex
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

from microsandbox import ExecEventType, MicrosandboxError, Sandbox, SandboxStatus

from lab.events import AgentEvent

IMAGE = "python:3.13"  # full image: git included (see the plan's base image decision)
REPO_DIR = "/lab/repo"
RUNS_DIR = "/lab/runs"
RUNNER_GUEST = "/lab/runner.py"
RUNNER_HOST = Path(__file__).parent / "runner" / "runner.py"
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


async def ensure_sandbox(session_id: UUID, repo_url: str) -> Sandbox:
    """Reattach to the session's running VM, or create one with a fresh clone."""
    _ensure_home()
    name = sandbox_name(session_id)
    try:
        handle = await Sandbox.get(name)
        if handle.status == SandboxStatus.RUNNING:
            return await handle.connect()
    except MicrosandboxError:
        pass  # no such sandbox yet
    sb = await Sandbox.create(name, image=IMAGE, detached=True, replace=True)
    # detach() consumes this handle; reconnecting gives one that does not own the VM.
    await sb.detach()
    sb = await (await Sandbox.get(name)).connect()
    try:
        await _check(
            sb, f"mkdir -p {RUNS_DIR} && git clone -q -- {shlex.quote(repo_url)} {REPO_DIR}"
        )
        await sb.fs.copy_from_host(str(RUNNER_HOST), RUNNER_GUEST)
    except Exception:
        await sb.destroy(force=True)  # a VM without a clone must not be reattached later
        raise
    return sb


async def exec_runner(sb: Sandbox, task: dict[str, Any]) -> AsyncIterator[AgentEvent]:
    """Run `runner.py` in the VM with `task` on stdin and yield its JSONL events."""
    stdin = json.dumps({**task, "runs_dir": RUNS_DIR}).encode()
    handle = await sb.exec_stream("python3", [RUNNER_GUEST], cwd=REPO_DIR, stdin=stdin)
    buffer = b""
    stderr = b""
    exit_code: int | None = None
    async for ev in handle:
        if ev.event_type == ExecEventType.STDOUT and ev.data:
            buffer += ev.data
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if line.strip():
                    data = json.loads(line)
                    yield AgentEvent(
                        data["type"], data["payload"], data["run_id"], data.get("rseq")
                    )
        elif ev.event_type == ExecEventType.STDERR and ev.data:
            stderr = (stderr + ev.data)[-STDERR_LIMIT:]
        elif ev.event_type == ExecEventType.EXITED:
            exit_code = ev.code
        elif ev.event_type == ExecEventType.FAILED:
            raise SandboxError(f"runner failed to start: {stderr.decode(errors='replace')}")
    if exit_code != 0:
        raise SandboxError(f"runner exited {exit_code}: {stderr.decode(errors='replace')}")


async def destroy(session_id: UUID) -> None:
    try:
        handle = await Sandbox.get(sandbox_name(session_id))
    except MicrosandboxError:
        return
    await handle.destroy(force=True)
