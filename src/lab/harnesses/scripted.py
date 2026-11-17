"""A deterministic fake agent that runs inside the VM, for tests.

It exercises the in-VM path (runner, JSONL events, event file) before a real
in-VM harness exists: it lists the repo, reads its README, runs pytest, answers.
"""

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Literal

from microsandbox import Sandbox

from lab import config
from lab.events import AgentEvent, Message
from lab.sandbox import VmSpec, exec_runner


class ScriptedHarness:
    name = "scripted"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use"})
    model = "scripted"  # no model: a fixed script

    def vm_spec(self) -> VmSpec:
        return VmSpec()  # the default public network: the runner `pip install`s pytest

    async def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str, resume: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        task = {
            "run_id": run_id,
            "harness": self.name,
            "transcript": [asdict(m) for m in transcript],
            "step_delay_s": config.SCRIPTED_STEP_DELAY_S,
        }
        async for event in exec_runner(sandbox, task):
            yield event
