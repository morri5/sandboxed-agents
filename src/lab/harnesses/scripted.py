"""A deterministic fake agent that runs inside the VM, for tests.

It exercises the in-VM path (runner, JSONL events, event file) before a real
in-VM harness exists: it lists the repo, reads its README, runs pytest, answers.
"""

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Literal

from microsandbox import Sandbox

from lab.events import AgentEvent, Message
from lab.sandbox import exec_runner


class ScriptedHarness:
    name = "scripted"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use"})

    async def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        task = {
            "run_id": run_id,
            "harness": self.name,
            "transcript": [asdict(m) for m in transcript],
        }
        async for event in exec_runner(sandbox, task):
            yield event
