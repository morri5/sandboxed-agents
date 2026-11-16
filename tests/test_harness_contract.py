"""The harness contract, checked the same way for both real harnesses, without a VM
or a model.

`deepagents_ollama` runs its real loop with a fake chat model, and its tool calls
go to a fake VM that answers every command. `claude_agent` and `claude_agent_local`
run their real host side and the real `runner.py` (as a local process instead of
a VM job) against a fake `claude_agent_sdk` (`tests/fakes/claude_agent_sdk.py`).
"""

import asyncio
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from microsandbox import Sandbox

from lab import config, pricing
from lab.events import TERMINAL_RUN_EVENTS, AgentEvent, Message
from lab.harnesses import HARNESSES
from lab.harnesses.base import CAPABILITIES, Harness
from lab.harnesses.claude_agent import ClaudeAgentHarness
from lab.harnesses.claude_agent_local import ClaudeAgentLocalHarness
from lab.harnesses.deepagents_ollama import DeepAgentsOllamaHarness
from lab.sandbox import RUNNER_HOST
from tests.fakes import claude_agent_sdk as fake_sdk

TRANSCRIPT = [Message("user", "What is in this repo?")]
RUN_ID = "run-1"


class ScriptedModel(BaseChatModel):
    """Lists the repo with one `execute` call, then answers."""

    turns: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> Any:
        self.turns += 1
        if self.turns == 1:
            call = {"name": "execute", "args": {"command": "ls"}, "id": "call-1"}
            message = AIMessage("", tool_calls=[call])
        else:
            message = AIMessage(fake_sdk.ANSWER)
        return ChatResult(generations=[ChatGeneration(message=message)])


@dataclass
class ExecOutput:
    stdout_text: str
    stderr_text: str
    exit_code: int


@dataclass
class LogEntry:
    source: str
    data: bytes


class FakeJob:
    def __init__(self, stdout: bytes, stderr: bytes, code: int) -> None:
        self.entries = [LogEntry("stdout", stdout), LogEntry("stderr", stderr)]
        self.code = code

    async def follow_logs(self) -> AsyncIterator[LogEntry]:
        async def entries() -> AsyncIterator[LogEntry]:
            for entry in self.entries:
                yield entry

        return entries()

    async def wait(self) -> "FakeJob":
        return self

    async def kill(self) -> None:
        pass


class FakeVM:
    """What the harnesses use of a microsandbox VM. `exec` answers every command;
    `exec_detached` runs the runner on the host with the fake SDK importable."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.sdk_dir = tmp / "sdk"
        self.sdk_dir.mkdir()
        shutil.copy(fake_sdk.__file__, self.sdk_dir / "claude_agent_sdk.py")

    @property
    def name(self) -> Awaitable[str]:
        async def name() -> str:
            return "fake-vm"

        return name()

    async def exec(self, cmd: str, args: list[str], **kwargs: Any) -> ExecOutput:
        return ExecOutput("README.md\ncalc\n", "", 0)

    async def exec_detached(self, cmd: str, args: list[str], *, cwd: str, stdin: bytes) -> FakeJob:
        task = {**json.loads(stdin), "runs_dir": str(self.tmp / "runs")}
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(RUNNER_HOST),
            *args[1:],
            cwd=self.tmp,
            env={**os.environ, "PYTHONPATH": str(self.sdk_dir)},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(json.dumps(task).encode())
        assert proc.returncode is not None
        return FakeJob(out, err, proc.returncode)


def _harness(name: str) -> Harness:
    if name == "deepagents_ollama":
        return DeepAgentsOllamaHarness(model=ScriptedModel())
    if name == "claude_agent_local":
        return ClaudeAgentLocalHarness()
    return ClaudeAgentHarness()


async def _run(harness: Harness, tmp_path: Path) -> list[AgentEvent]:
    vm = cast(Sandbox, FakeVM(tmp_path))
    return [ev async for ev in harness.run(TRANSCRIPT, vm, RUN_ID)]


@pytest.mark.parametrize("name", ["deepagents_ollama", "claude_agent", "claude_agent_local"])
async def test_harness_contract(name: str, tmp_path: Path) -> None:
    harness = _harness(name)
    assert harness.name == name and HARNESSES[name].name == name
    assert harness.placement in ("host", "sandbox")
    assert harness.capabilities <= CAPABILITIES

    produced = await _run(harness, tmp_path)
    durable = [ev for ev in produced if ev.rseq is not None]
    deltas = [ev for ev in produced if ev.rseq is None]

    # Durable events are numbered 1..n within the run and end with exactly one terminal event.
    assert all(ev.run_id == RUN_ID for ev in produced)
    assert [ev.rseq for ev in durable] == list(range(1, len(durable) + 1))
    types = [ev.type for ev in durable]
    assert types[-1] in TERMINAL_RUN_EVENTS
    assert sum(t in TERMINAL_RUN_EVENTS for t in types) == 1
    assert types[-1] == "run_finished", durable[-1]

    # Tool calls come before their results; one answer, before the end.
    assert "tool_call" in types and "tool_result" in types
    calls = [ev.payload["id"] for ev in durable if ev.type == "tool_call"]
    for i, ev in enumerate(durable):
        if ev.type == "tool_result":
            assert ev.payload["id"] in [e.payload.get("id") for e in durable[:i]], ev
    assert len(calls) == len(set(calls))
    assert types.count("agent_message") == 1
    assert fake_sdk.ANSWER in durable[types.index("agent_message")].payload["text"]

    # Live tokens are deltas only, never durable.
    assert all(ev.type == "agent_delta" for ev in deltas)
    if "streaming" in harness.capabilities and name.startswith("claude_agent"):
        assert "".join(ev.payload["text"] for ev in deltas).endswith(fake_sdk.ANSWER)

    finished = durable[-1].payload
    if "cost_report" in harness.capabilities:
        # The local brain is free; DeepSeek is priced from `pricing.py`.
        free = name == "claude_agent_local"
        expected = 0.0 if free else pricing.cost_usd(config.DEEPSEEK_MODEL, finished["usage"])
        assert finished["cost_usd"] == pytest.approx(expected)
        assert finished["usage"]["input_tokens"] == 40_200
    if "native_resume" in harness.capabilities:
        assert finished["resume"] == "fake-session"


async def test_budget_ends_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LAB_MAX_USD_PER_RUN=0.01: the fake's first turn costs about $0.012 at the
    deepseek-flash price, so the client is interrupted before the tool runs."""
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(config, "MAX_USD_PER_RUN", 0.01)
    durable = [ev for ev in await _run(ClaudeAgentHarness(), tmp_path) if ev.rseq is not None]
    assert [ev.type for ev in durable] == ["tool_call", "run_failed"]
    failed = durable[-1].payload
    assert failed["reason"] == "budget"
    assert failed["limit_usd"] == 0.01
    assert failed["cost_usd"] > 0.01
