"""Runs inside the VM. Reads one JSON task on stdin, writes JSONL events.

Every durable event gets the run id and a per-run counter `rseq`, is appended to
`<runs_dir>/<run_id>.jsonl` and flushed first, and only then printed on stdout.
The file is what survives if the API that reads stdout dies. Live `agent_delta`
tokens go to stdout only.

Agents: `scripted`, a deterministic fake, and the Claude Agent SDK, with DeepSeek
(`claude_agent`) or the host's Ollama (`claude_agent_local`) as the brain. Standard
library only, apart from the SDK, which only those VMs have (mounted read-only, see
`harnesses/claude_agent.py`).
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any

OUTPUT_LIMIT = 16_000  # characters kept per tool output (the tail, where summaries are)
TERMINAL = ("run_finished", "run_failed", "run_interrupted")


class Emitter:
    def __init__(self, run_id: str, log: IO[str]) -> None:
        self.run_id = run_id
        self.log = log
        self.rseq = 0
        self.ended = False  # a terminal event was emitted

    def event(self, type: str, payload: dict[str, Any]) -> None:
        self.rseq += 1
        self.ended = self.ended or type in TERMINAL
        line = json.dumps(
            {"run_id": self.run_id, "rseq": self.rseq, "type": type, "payload": payload}
        )
        self.log.write(line + "\n")
        self.log.flush()
        print(line, flush=True)

    def delta(self, text: str) -> None:
        line = json.dumps({"run_id": self.run_id, "type": "agent_delta", "payload": {"text": text}})
        print(line, flush=True)


def tail(text: str) -> str:
    return text[-OUTPUT_LIMIT:]


def shell(emit: Emitter, call_id: str, command: str, timeout: float = 600) -> dict[str, Any]:
    emit.event("tool_call", {"id": call_id, "name": "shell", "args": {"command": command}})
    try:
        proc = subprocess.run(
            ["sh", "-c", command], capture_output=True, text=True, timeout=timeout
        )
        result = {
            "stdout": tail(proc.stdout),
            "stderr": tail(proc.stderr),
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        result = {"stdout": "", "stderr": f"timed out after {timeout} s", "exit_code": -1}
    emit.event("tool_result", {"id": call_id, "name": "shell", **result})
    return result


def read_file(emit: Emitter, call_id: str, path: str) -> str:
    emit.event("tool_call", {"id": call_id, "name": "read_file", "args": {"path": path}})
    content = tail(Path(path).read_text(errors="replace"))
    emit.event("tool_result", {"id": call_id, "name": "read_file", "content": content})
    return content


def scripted(emit: Emitter, task: dict[str, Any]) -> None:
    """A deterministic fake agent: look around, read a file, run the tests, answer."""
    question = next((m["text"] for m in reversed(task["transcript"]) if m["role"] == "user"), "")
    delay = task.get("step_delay_s", 0)  # slows the steps down, for the crash tests
    time.sleep(delay)
    listing = shell(emit, "call-1", "ls -A")["stdout"].split()
    readmes = sorted(name for name in listing if name.lower().startswith("readme"))
    readme = readmes[0] if readmes else None
    if readme:
        time.sleep(delay)
        read_file(emit, "call-2", readme)
    time.sleep(delay)
    shell(
        emit,
        "call-3",
        "python -m pip install -q --disable-pip-version-check --root-user-action=ignore"
        " pytest -e .",
    )
    time.sleep(delay)
    tests = shell(emit, "call-4", "python -m pytest -q")
    lines = tests["stdout"].strip().splitlines()
    summary = lines[-1] if lines else "no output"
    answer = (
        f'You asked: "{question}". The repo has {len(listing)} top-level entries'
        + (f", I read {readme}" if readme else "")
        + f", and pytest exited {tests['exit_code']}: {summary}"
    )
    for word in answer.split(" "):
        emit.delta(word + " ")
    emit.event("agent_message", {"text": answer})


def usd(usage: dict[str, Any], price: dict[str, float]) -> float:
    """Cost of an Anthropic-style `usage` at `price` (USD per million tokens).

    Tokens written to the cache are billed as input (DeepSeek caches on its own).
    """
    tokens = (
        (usage.get("input_tokens") or 0) * price["input"]
        + (usage.get("cache_creation_input_tokens") or 0) * price["input"]
        + (usage.get("cache_read_input_tokens") or 0) * price["cache_read"]
        + (usage.get("output_tokens") or 0) * price["output"]
    )
    return tokens / 1_000_000


USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
CLAUDE_TOOLS = ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
# Subagents would hide their tool calls inside one result; the web tools cannot
# work behind a network policy that only allows the model API.
CLAUDE_DISALLOWED = ["Task", "Agent", "WebFetch", "WebSearch"]


def claude_prompt(transcript: list[dict[str, str]], resume: str | None) -> str:
    """The last user message, preceded by the earlier conversation unless the
    harness resumes its own session (which already holds it)."""
    last = transcript[-1]["text"]
    if resume or len(transcript) == 1:
        return last
    earlier = "\n\n".join(f"{m['role']}: {m['text']}" for m in transcript[:-1])
    return (
        "The conversation so far, rebuilt from the session log:\n\n"
        f"<conversation>\n{earlier}\n</conversation>\n\nThe user's new message:\n\n{last}"
    )


HOST_CID = 2  # the host, as seen over vsock


def vsock_relay(port: int) -> int:
    """Listen on a free loopback port and relay each connection to the host's vsock
    `port`; returns the loopback port. The CLI only speaks TCP, and a VM without
    a network reaches the host's Ollama bridge only over vsock."""
    server = socket.create_server(("127.0.0.1", 0))

    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            src.close()
            dst.close()

    def serve() -> None:
        while True:
            client, _ = server.accept()
            upstream = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)  # type: ignore[attr-defined]  # Linux guest only
            try:
                upstream.connect((HOST_CID, port))
            except OSError:  # no bridge (the API is down): the CLI sees a closed connection
                client.close()
                upstream.close()
                continue
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return int(server.getsockname()[1])


def claude_agent(emit: Emitter, task: dict[str, Any]) -> dict[str, Any] | None:
    return asyncio.run(_claude_agent(emit, task))


async def _claude_agent(emit: Emitter, task: dict[str, Any]) -> dict[str, Any] | None:
    """The Claude Agent SDK on the task, as events; returns the `run_finished` payload.

    Cost is added up after every model turn from the usage in the turn's
    `message_delta` stream event; over the budget (if there is one) the client is
    interrupted and the run ends with `run_failed{reason: "budget"}`. `max_turns`
    is the hard cap in case usage is missing. With `vsock_port` the brain is the
    host's Ollama, reached through `vsock_relay`.
    """
    from claude_agent_sdk import (  # only claude_agent VMs have the SDK
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        StreamEvent,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    model, price, budget = task["model"], task["price"], task["budget_usd"]
    env = {"ANTHROPIC_DEFAULT_HAIKU_MODEL": model}  # background calls never ask for Claude
    if task.get("vsock_port"):
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{vsock_relay(task['vsock_port'])}"
    options = ClaudeAgentOptions(
        cwd=os.getcwd(),
        model=model,
        system_prompt={"type": "preset", "preset": "claude_code", "append": task["system_prompt"]},
        allowed_tools=CLAUDE_TOOLS,
        disallowed_tools=CLAUDE_DISALLOWED,
        permission_mode="dontAsk",  # the VM is the boundary; anything not allowed is denied
        max_turns=task["max_turns"],
        resume=task.get("resume"),
        include_partial_messages=True,
        env=env,
    )
    spent, over_budget, result = 0.0, False, None
    names: dict[str, str] = {}
    async with ClaudeSDKClient(options) as client:
        await client.query(claude_prompt(task["transcript"], task.get("resume")))
        async for msg in client.receive_response():
            if isinstance(msg, StreamEvent):
                event = msg.event
                delta = event.get("delta") or {}
                if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    emit.delta(delta["text"])
                elif event.get("type") == "message_delta" and not over_budget:
                    spent += usd(event.get("usage") or {}, price)
                    if budget is not None and spent > budget:
                        over_budget = True
                        await client.interrupt()
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        names[block.id] = block.name
                        payload = {"id": block.id, "name": block.name, "args": block.input}
                        emit.event("tool_call", payload)
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        content = block.content
                        if not isinstance(content, str):  # a list of content blocks
                            content = "".join(c.get("text", "") for c in content or [])
                        emit.event(
                            "tool_result",
                            {
                                "id": block.tool_use_id,
                                "name": names.get(block.tool_use_id),
                                "content": tail(content),
                                "is_error": bool(block.is_error),
                            },
                        )
            elif isinstance(msg, ResultMessage):
                result = msg
    if result is None:
        raise RuntimeError("the SDK ended without a result message")
    usage = {k: (result.usage or {}).get(k) or 0 for k in USAGE_KEYS}
    cost = {"usage": usage, "cost_usd": round(max(spent, usd(usage, price)), 6)}
    if over_budget:
        emit.event("run_failed", {"reason": "budget", "limit_usd": budget, **cost})
    elif result.subtype == "error_max_turns":
        emit.event("run_failed", {"reason": "max_turns", "limit": task["max_turns"], **cost})
    elif result.is_error or result.subtype != "success":
        error = result.errors or result.result or result.subtype
        emit.event("run_failed", {"reason": "error", "error": str(error)[-2000:], **cost})
    else:
        emit.event("agent_message", {"text": result.result or ""})
        return {
            "model": model,
            "num_turns": result.num_turns,
            "resumed": bool(task.get("resume")),
            "resume": result.session_id,  # the SDK session, for a native resume next run
            **cost,
        }
    return None


HARNESSES = {"scripted": scripted, "claude_agent": claude_agent, "claude_agent_local": claude_agent}


def main() -> None:
    # The run id also arrives as argv[1], only so the API can find this job again.
    task = json.load(sys.stdin)
    runs = Path(task["runs_dir"])
    runs.mkdir(parents=True, exist_ok=True)
    with (runs / f"{task['run_id']}.jsonl").open("a") as log:
        emit = Emitter(task["run_id"], log)
        try:
            finished = HARNESSES[task["harness"]](emit, task)
        except Exception as exc:  # report any agent failure as an event, then exit non-zero
            emit.event("run_failed", {"reason": "runner_error", "error": repr(exc)})
            sys.exit(1)
        if not emit.ended:
            emit.event("run_finished", finished or {})


if __name__ == "__main__":
    main()
