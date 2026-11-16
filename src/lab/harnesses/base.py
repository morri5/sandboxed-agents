"""The harness contract shared by every agent harness."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from microsandbox import Sandbox

from lab.events import AgentEvent, Message


class Harness(Protocol):
    name: str
    placement: Literal["host", "sandbox"]
    capabilities: frozenset[
        str
    ]  # {"streaming", "tool_use", "native_resume", "cost_report", "budget"}

    def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str
    ) -> AsyncIterator[AgentEvent]: ...
