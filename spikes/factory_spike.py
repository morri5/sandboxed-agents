"""Spike: does a real slash command, with its subagents, run headless in a VM on DeepSeek?

Runs `research_codebase.md` from `LAB_FACTORY_COMMANDS_DIR` (frontmatter stripped,
a short headless contract in front) on `fixtures/factory-repo` through the session
API with the `claude_factory` harness, whose subagents come from
`LAB_FACTORY_AGENTS_DIR`. Then it checks what came out (run end, `Agent` calls,
a research document in the saved workspace) and compares the cost the run reports
with the drop in the DeepSeek account balance, to find out whether the reported
usage includes the subagents' model calls. The balance has cent resolution, so the
delta only bounds a run that costs a few cents.

Starts its own API on a free port, like `make demo`: stop `make api` first.
Exits 1 if the run did not finish, called no subagent or wrote no research document.
"""

import asyncio
import base64
import datetime
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from _common import write_result

ROOT = Path(__file__).resolve().parents[1]
REPO = str(ROOT / "fixtures" / "factory-repo")
COMMANDS = Path(os.environ.get("LAB_FACTORY_COMMANDS_DIR", "~/.claude/commands")).expanduser()
QUESTION = "How is the calculator's add function tested?"
LIMITS = {"max_tool_calls": 150, "run_timeout_s": 1800, "max_usd": 1.0}
CONTRACT = """No human is available during this run. Never ask questions or wait for input:
where the instructions below say to ask, wait or get confirmation, decide yourself,
write the assumption into the document, and continue. Skip `humanlayer thoughts sync`
and any script that is not in this repository. Where the instructions say to spawn
sub-agents, do so with the Agent tool as they describe; do not do their work yourself.
Write the research document under thoughts/shared/research/ in this repository."""
BALANCE_SETTLE_S = 180  # DeepSeek books usage with a delay


def command_text(name: str, arguments: str) -> str:
    text = (COMMANDS / f"{name}.md").read_text()
    if text.startswith("---\n"):
        text = text[4:].partition("\n---\n")[2]
    return f"{CONTRACT}\n\n{text.strip()}\n\nARGUMENTS: {arguments}"


def balance_usd() -> Decimal:
    r = httpx.get(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
        timeout=30,
    )
    r.raise_for_status()
    infos = [b for b in r.json()["balance_infos"] if b["currency"] == "USD"]
    return Decimal(infos[0]["total_balance"])


def start_api() -> tuple[subprocess.Popen[bytes], str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", str(port)]
        + ["--log-level", "warning"],
        cwd=ROOT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"{url}/docs").raise_for_status()
            return proc, url
        except httpx.HTTPError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise
            time.sleep(0.1)


async def run_session(url: str, text: str) -> list[dict[str, Any]]:
    """One message on a new session; the log once the session is closed."""
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        body = {"repo_url": REPO, "harness": "claude_factory"}
        session_id = (await client.post("/sessions", json=body)).raise_for_status().json()
        session_id = session_id["session_id"]
        print(f"session {session_id}")
        message = {
            "text": text,
            "idem_key": str(uuid.uuid4()),
            "fresh_context": True,
            "limits": LIMITS,
        }
        (await client.post(f"/sessions/{session_id}/messages", json=message)).raise_for_status()
        (await client.delete(f"/sessions/{session_id}")).raise_for_status()
        async with asyncio.timeout(LIMITS["run_timeout_s"] + 300):
            while True:
                await asyncio.sleep(5)
                log: list[dict[str, Any]] = (
                    (await client.get(f"/sessions/{session_id}/events")).raise_for_status().json()
                )
                if any(e["type"] in ("session_closed", "session_failed") for e in log):
                    return log


def added_files(patch: bytes) -> list[str]:
    """Paths of the files the patch creates."""
    found = re.findall(rb"^diff --git a/(\S+) b/\S+\nnew file mode", patch, re.MULTILINE)
    return [p.decode() for p in found]


def seconds(start: dict[str, Any], end: dict[str, Any]) -> float:
    t0 = datetime.datetime.fromisoformat(start["created_at"])
    t1 = datetime.datetime.fromisoformat(end["created_at"])
    return round((t1 - t0).total_seconds(), 1)


def settled_balance(before: Decimal) -> Decimal:
    """The balance once it has dropped below `before` and stayed put for 30 s."""
    deadline = time.monotonic() + BALANCE_SETTLE_S
    last = balance_usd()
    while time.monotonic() < deadline:
        time.sleep(30)
        now = balance_usd()
        if now == last and now < before:
            return now
        last = now
    return last


def main() -> None:
    text = command_text("research_codebase", QUESTION)
    before = balance_usd()
    proc, url = start_api()
    try:
        log = asyncio.run(run_session(url, text))
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    after = settled_balance(before)

    terminal = next(
        e for e in log if e["type"] in ("run_finished", "run_failed", "run_interrupted")
    )
    started = next(e for e in log if e["type"] == "run_started")
    calls = [e["payload"] for e in log if e["type"] == "tool_call"]
    saved = [e for e in log if e["type"] == "workspace_saved"]
    patch = base64.b64decode(saved[-1]["payload"]["patch_b64"]) if saved else b""
    thoughts = [p for p in added_files(patch) if p.startswith("thoughts/")]
    reported = terminal["payload"].get("cost_usd")
    delta = float(before - after)
    result = {
        "session_id": log[0]["session_id"],
        "terminal": terminal["type"],
        "terminal_payload": {k: v for k, v in terminal["payload"].items() if k != "usage"},
        "usage": terminal["payload"].get("usage"),
        "cost_usd": reported,
        "num_turns": terminal["payload"].get("num_turns"),
        "tool_calls": len(calls),
        "tool_calls_with_parent": sum(c.get("parent") is not None for c in calls),
        "subagents_called": [c["args"].get("subagent_type") for c in calls if c["name"] == "Agent"],
        "tool_names": sorted({c["name"] for c in calls}),
        "thoughts_files_added": thoughts,
        "wall_s": seconds(started, terminal),
        "balance_before_usd": str(before),
        "balance_after_usd": str(after),
        "balance_delta_usd": round(delta, 6),
        "reported_over_delta": round(reported / delta, 3) if reported and delta > 0 else None,
    }
    write_result("factory_spike", result)
    print(f"terminal: {result['terminal']}, wall {result['wall_s']} s")
    print(f"tool calls: {len(calls)} ({result['tool_calls_with_parent']} by subagents)")
    print(f"subagents: {result['subagents_called']}")
    print(f"thoughts files: {thoughts}")
    print(f"reported cost_usd: {reported}")
    payload = terminal["payload"]
    print(f"  from stream events: {payload.get('stream_cost_usd')}")
    print(f"  from the result's usage: {payload.get('result_cost_usd')}")
    print(f"balance delta: {result['balance_delta_usd']}")
    research = [p for p in thoughts if p.startswith("thoughts/shared/research/")]
    ok = terminal["type"] == "run_finished" and result["subagents_called"] and research
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
