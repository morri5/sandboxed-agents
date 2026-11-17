"""Architecture A: a deepagents loop on the host, Ollama as the brain, tools in the VM.

The loop runs as part of the API process and talks to Ollama on 127.0.0.1. Every
file and shell operation goes through `MicrosandboxBackend` into the session's
VM, which has no network. There is no LangGraph checkpointer: each run starts
from the transcript folded from the event log.

LangGraph's `messages` stream gives the live `agent_delta` tokens; its `updates`
stream gives whole messages, which become the durable `tool_call`,
`tool_result` and `agent_message` events.
"""

from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Any, Literal, cast

from deepagents import create_deep_agent
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_ollama import ChatOllama
from microsandbox import Network, Sandbox

from lab import config
from lab.events import AgentEvent, Message
from lab.harnesses.microsandbox_backend import MicrosandboxBackend
from lab.runner.runner import OUTPUT_LIMIT
from lab.sandbox import REPO_DIR, VmSpec

# No general-purpose subagent (the `task` tool) for Ollama models: its tool calls
# would run inside one opaque `task` result, invisible to the event log and to the
# tool-call limit.
register_harness_profile(
    "ollama",
    HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
)

SYSTEM_PROMPT = f"""You are a coding agent working on a git repository at {REPO_DIR}.
Use absolute paths under {REPO_DIR} for file tools. Shell commands run in {REPO_DIR}
inside an isolated Linux VM with Python 3.13 and git, and without network access,
so nothing can be installed. Investigate before you change code, run the tests to
check your work, and finish with a short answer that says what you found or changed."""


class DeepAgentsOllamaHarness:
    name = "deepagents_ollama"
    placement: Literal["host", "sandbox"] = "host"
    capabilities = frozenset({"streaming", "tool_use"})
    model = config.OLLAMA_MODEL

    def __init__(
        self, model: BaseChatModel | None = None, max_tool_calls: int = config.MAX_TOOL_CALLS
    ) -> None:
        self.chat_model = model  # a fake for tests; None means Ollama
        self.max_tool_calls = max_tool_calls

    def _model(self) -> BaseChatModel:
        if self.chat_model is not None:
            return self.chat_model
        return ChatOllama(
            model=config.OLLAMA_MODEL, base_url=config.OLLAMA_URL, num_ctx=config.OLLAMA_NUM_CTX
        )

    def vm_spec(self) -> VmSpec:
        return VmSpec(network=Network.none())  # tools only: the VM never talks to anything

    async def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str, resume: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        backend = MicrosandboxBackend(sandbox, await sandbox.name)
        agent = create_deep_agent(model=self._model(), backend=backend, system_prompt=SYSTEM_PROMPT)
        rseq = 0

        def durable(type: str, payload: dict[str, Any]) -> AgentEvent:
            nonlocal rseq
            rseq += 1
            return AgentEvent(type, payload, run_id, rseq)

        calls = 0
        usage: Counter[str] = Counter()
        stream = cast(
            AsyncGenerator[tuple[str, Any]],  # with a list of stream modes: (mode, chunk)
            agent.astream(
                {"messages": [{"role": m.role, "content": m.text} for m in transcript]},
                stream_mode=["messages", "updates"],
                # A backstop only: the tool-call limit below is meant to end the run first.
                config={"recursion_limit": 10 * self.max_tool_calls + 50},
            ),
        )
        async with aclosing(stream):  # leaving early cancels the loop before pending calls run
            async for mode, chunk in stream:
                if mode == "messages":
                    token = chunk[0]
                    if isinstance(token, AIMessageChunk) and token.text:
                        yield AgentEvent("agent_delta", {"text": token.text}, run_id)
                    continue
                for msg in _new_messages(chunk):
                    if isinstance(msg, ToolMessage):
                        yield durable("tool_result", _tool_result(msg))
                        continue
                    usage.update(_usage(msg))
                    if not msg.tool_calls:
                        yield durable("agent_message", {"text": msg.text})
                        continue
                    if calls + len(msg.tool_calls) > self.max_tool_calls:
                        limit = self.max_tool_calls
                        yield durable("run_failed", {"reason": "max_tool_calls", "limit": limit})
                        return
                    for call in msg.tool_calls:
                        calls += 1
                        payload = {"id": call["id"], "name": call["name"], "args": call["args"]}
                        yield durable("tool_call", payload)
        yield durable("run_finished", {"tool_calls": calls, "usage": dict(usage)})


def _new_messages(update: dict[str, Any]) -> list[AIMessage | ToolMessage]:
    """The messages a model or tools step added; middleware steps add none."""
    return [
        msg
        for node, value in update.items()
        if node in ("model", "tools") and isinstance(value, dict)
        for msg in value.get("messages", [])
        if isinstance(msg, AIMessage | ToolMessage)
    ]


def _usage(msg: AIMessage) -> dict[str, int]:
    u = msg.usage_metadata
    if not u:
        return {}
    return {
        "input_tokens": u["input_tokens"],
        "output_tokens": u["output_tokens"],
        "total_tokens": u["total_tokens"],
    }


def _tool_result(msg: ToolMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": msg.tool_call_id,
        "name": msg.name,
        "content": msg.text[-OUTPUT_LIMIT:],
        "status": msg.status,
    }
    if isinstance(msg.artifact, dict) and "exit_code" in msg.artifact:
        payload["exit_code"] = msg.artifact["exit_code"]  # set by the `execute` tool
    return payload
