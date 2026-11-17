"""Run the eval: every task, N times per harness, through the normal session API.

    python -m lab.evals.run --harness deepagents_ollama,claude_agent [--repeats 3] [--tasks a,b]

It starts its own API process (stop `make api` first: two APIs on one database
would both reconcile the same sessions), with one VM size for every harness. Each
trial is a fresh session on the task's `environment/`: the instruction is sent
as the one message, the session is closed right behind it (the close is queued
after the run and its workspace snapshot), and the last `workspace_saved` patch
is the agent's final diff, which the grader judges in a VM of its own.

Each trial is appended to `evals/results/<date>.jsonl`; the Markdown report
next to it is rebuilt from that whole file (all runs of the day) at the end.
"""

import argparse
import asyncio
import base64
import datetime
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from lab import config, pricing
from lab.db import DATABASE_URL, migrate
from lab.evals import report
from lab.evals.grader import Verdict, grade
from lab.evals.tasks import Task, load_all
from lab.harnesses import HARNESSES
from lab.sandbox import EVAL_TASKS

RESULTS = EVAL_TASKS.parent / "results"
# Every harness gets the same VM: `claude_agent` VMs always have 1 GiB, so all do.
VM_MEMORY_MIB = 1024
ATTEMPTS = 3  # tries per trial (and per grading) while the failure is an infra error
POLL_S = 1.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def start_api(port: int) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", str(port)]
        + ["--log-level", "warning"],
        env={**os.environ, "LAB_VM_MEMORY_MIB": str(VM_MEMORY_MIB)},
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"http://127.0.0.1:{port}/docs").raise_for_status()
            return proc
        except httpx.HTTPError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise
            time.sleep(0.1)


def _scrub(text: str) -> str:
    """Error text for the results file, which may be published: no home directory in it."""
    return text.replace(str(Path.home()), "~")[-300:]


def _tokens(usage: dict[str, Any]) -> dict[str, int]:
    return {
        "input": usage.get("input_tokens") or 0,
        "output": usage.get("output_tokens") or 0,
        "cache_read": usage.get("cache_read_input_tokens") or 0,
        "cache_write": usage.get("cache_creation_input_tokens") or 0,
    }


def _seconds(start: dict[str, Any], end: dict[str, Any]) -> float:
    t0 = datetime.datetime.fromisoformat(start["created_at"])
    t1 = datetime.datetime.fromisoformat(end["created_at"])
    return round((t1 - t0).total_seconds(), 2)


async def _session(client: httpx.AsyncClient, task: Task, harness: str) -> list[dict[str, Any]]:
    """One agent session on the task, closed after its run; returns its log."""
    body = {"repo_url": str(task.environment), "harness": harness}
    session_id = (await client.post("/sessions", json=body)).raise_for_status().json()["session_id"]
    message = {"text": task.instruction, "idem_key": str(uuid.uuid4())}
    (await client.post(f"/sessions/{session_id}/messages", json=message)).raise_for_status()
    (await client.delete(f"/sessions/{session_id}")).raise_for_status()
    async with asyncio.timeout(config.RUN_TIMEOUT_S + 300):
        while True:
            await asyncio.sleep(POLL_S)
            log: list[dict[str, Any]] = (
                (await client.get(f"/sessions/{session_id}/events")).raise_for_status().json()
            )
            if any(e["type"] in ("session_closed", "session_failed") for e in log):
                return log


def _infra_error(log: list[dict[str, Any]]) -> str | None:
    """Why the VM could not be set up, if that is how the session ended."""
    for e in log:
        if e["type"] == "session_failed" or (
            e["type"] == "sandbox_lost" and e["payload"].get("reason") == "provisioning_failed"
        ):
            return str(e["payload"].get("error") or e["payload"].get("reason"))
    return None


async def _grade(task: Task, patch: bytes) -> tuple[Verdict, list[str]]:
    errors = []
    for _ in range(ATTEMPTS):
        verdict = await grade(task, patch)
        if verdict.status != "infra_error":
            break
        errors.append(_scrub(f"grader: {verdict.reason}: {verdict.log}"))
    return verdict, errors


async def trial(client: httpx.AsyncClient, task: Task, harness: str, repeat: int) -> dict[str, Any]:
    h = HARNESSES[harness]
    infra: list[str] = []
    for _ in range(ATTEMPTS):
        log = await _session(client, task, harness)
        error = _infra_error(log)
        if error is None:
            break
        infra.append(_scrub(f"agent vm: {error}"))
    record: dict[str, Any] = {
        "task": task.name,
        "harness": harness,
        "model": h.model,
        "repeat": repeat,
        "session_id": log[0]["session_id"],
        "infra_errors": infra,
    }
    if error is not None:
        return {**record, "status": "infra_error", "passed": False}
    started = next(e for e in log if e["type"] == "run_started")
    end = [e for e in log if e["type"] in ("run_finished", "run_failed", "run_interrupted")][-1]
    saved = [e for e in log if e["type"] == "workspace_saved"]
    patch = base64.b64decode(saved[-1]["payload"]["patch_b64"]) if saved else b""
    verdict, grader_errors = await _grade(task, patch)
    cost = end["payload"].get("cost_usd")
    if cost is None and "cost_report" not in h.capabilities:
        cost = 0.0  # a local model; None means the run ended without reporting its cost
    return {
        **record,
        "infra_errors": infra + grader_errors,
        "status": verdict.status,
        "passed": verdict.passed,
        "grade": {"reason": verdict.reason, "failed": list(verdict.failed), "s": verdict.seconds},
        "run_end": end["type"],
        "run_reason": end["payload"].get("reason"),
        "tool_calls": sum(e["type"] == "tool_call" for e in log),
        # None: the run was cut off (timeout) before it reported its usage
        "tokens": _tokens(end["payload"]["usage"]) if end["payload"].get("usage") else None,
        "cost_usd": cost,
        "pricing_checked": pricing.CHECKED if cost else None,
        "wall_s": _seconds(started, end),
        "diff_bytes": len(patch),
    }


async def main(harnesses: list[str], tasks: list[Task], repeats: int) -> Path:
    unknown = set(harnesses) - set(HARNESSES)
    if unknown:
        raise SystemExit(f"unknown harness {sorted(unknown)}, known: {sorted(HARNESSES)}")
    for name in harnesses:
        HARNESSES[name].vm_spec()  # fail now on a missing key or SDK, not in every trial
    await migrate(DATABASE_URL)
    RESULTS.mkdir(exist_ok=True)
    now = datetime.datetime.now()
    out = RESULTS / f"{now.date().isoformat()}.jsonl"
    meta = {
        "eval_id": now.strftime("%Y%m%dT%H%M%S"),
        "vm_cpus": config.VM_CPUS,
        "vm_memory_mib": VM_MEMORY_MIB,
        "run_timeout_s": config.RUN_TIMEOUT_S,
        "exec_timeout_s": config.EXEC_TIMEOUT_S,
        "max_tool_calls": config.MAX_TOOL_CALLS,
    }
    port = _free_port()
    api = start_api(port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            for harness in harnesses:
                for task in tasks:
                    for repeat in range(1, repeats + 1):
                        result = {**meta, **await trial(client, task, harness, repeat)}
                        with out.open("a") as f:
                            f.write(json.dumps(result) + "\n")
                        print(
                            f"{harness} {task.name} #{repeat}: {result['status']}"
                            f" ({result.get('grade', {}).get('reason', '')},"
                            f" {result.get('wall_s')} s, session {result['session_id']})",
                            flush=True,
                        )
    finally:
        api.terminate()
        api.wait(timeout=30)
    path = report.write(out)
    print(f"results: {out}\nreport: {path}")
    return path


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--harness", required=True, help="comma-separated harness names")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tasks", default="", help="comma-separated task names (default: all)")
    args = parser.parse_args()
    tasks = load_all([t for t in args.tasks.split(",") if t] or None)
    asyncio.run(main([h for h in args.harness.split(",") if h], tasks, args.repeats))


if __name__ == "__main__":
    cli()
