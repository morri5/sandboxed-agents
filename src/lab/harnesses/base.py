"""The harness contract shared by every agent harness."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from microsandbox import Sandbox

from lab.events import AgentEvent, Limits, Message
from lab.sandbox import VmSpec

# What shared code may check instead of the harness's type:
#   streaming      live `agent_delta` tokens
#   tool_use       tool calls and results as durable events
#   native_resume  can continue its own session in the same VM (`resume` in `run`)
#   cost_report    `run_finished` carries `cost_usd`
#   budget         stops itself at `LAB_MAX_USD_PER_RUN` with `run_failed{reason: "budget"}`
#   limits         takes a message's `Limits` (`run` of `LimitedHarness`)
CAPABILITIES = frozenset(
    {"streaming", "tool_use", "native_resume", "cost_report", "budget", "limits"}
)


class Harness(Protocol):
    name: str
    placement: Literal["host", "sandbox"]
    capabilities: frozenset[str]  # a subset of CAPABILITIES
    model: str  # the brain, as configured when the process started

    def vm_spec(self) -> VmSpec:
        """The VM this harness needs (network policy, memory, secrets, mounts)."""
        ...

    def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str, resume: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Run on `transcript`, which ends with the user message to answer.

        `resume` is only passed to a harness with `native_resume`, and only when its
        previous run in this same VM finished: then it is the token that run reported
        in `run_finished.resume`, and the harness may continue from its own session
        instead of the transcript.
        """
        ...


class LimitedHarness(Harness, Protocol):
    """A harness with the `limits` capability: `run` also takes the message's limits."""

    def run(
        self,
        transcript: list[Message],
        sandbox: Sandbox,
        run_id: str,
        resume: str | None = None,
        limits: Limits | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """As `Harness.run`; `limits` overrides `max_tool_calls` and `max_usd` from `config`."""
        ...
