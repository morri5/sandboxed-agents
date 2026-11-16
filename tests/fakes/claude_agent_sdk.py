"""A stand-in for `claude_agent_sdk`, for tests without a VM or a model.

The contract tests copy this file into a temp dir as `claude_agent_sdk.py` and put
that dir on the runner's PYTHONPATH. The client replays one short recorded-shape
run: a turn that lists the repo with Bash, then a turn that answers. The message
shapes follow what the real SDK (0.2.182) produced with DeepSeek: one
`AssistantMessage` per content block, per-turn usage in the `message_delta`
stream event, totals in the `ResultMessage`.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

TURN_1 = {"input_tokens": 40_000, "cache_read_input_tokens": 0, "output_tokens": 100}
TURN_2 = {"input_tokens": 200, "cache_read_input_tokens": 40_000, "output_tokens": 20}
ANSWER = "The repo has two entries."


class ClaudeAgentOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.model = kwargs.pop("model", None)
        self.resume = kwargs.pop("resume", None)
        self.max_turns = kwargs.pop("max_turns", None)
        self.extra = kwargs


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str


@dataclass
class UserMessage:
    content: str | list[Any]


@dataclass
class StreamEvent:
    uuid: str
    session_id: str
    event: dict[str, Any]


@dataclass
class ResultMessage:
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    usage: dict[str, Any] | None = None
    result: str | None = None
    errors: list[str] | None = None


def _stream(event: dict[str, Any]) -> StreamEvent:
    return StreamEvent("u", "fake-session", event)


def _text(text: str) -> StreamEvent:
    return _stream({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


def _total(*turns: dict[str, int]) -> dict[str, int]:
    return {k: sum(t[k] for t in turns) for k in turns[0]}


class ClaudeSDKClient:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.interrupted = False

    async def __aenter__(self) -> "ClaudeSDKClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def interrupt(self) -> None:
        self.interrupted = True

    async def receive_response(self) -> AsyncIterator[Any]:
        model = self.options.model or "fake"
        turn_1 = [
            _text("Let "),
            _text("me look."),
            AssistantMessage([TextBlock("Let me look.")], model),
            AssistantMessage([ToolUseBlock("call-1", "Bash", {"command": "ls"})], model),
            _stream({"type": "message_delta", "usage": TURN_1}),
        ]
        for item in turn_1:
            yield item
        if self.interrupted:  # the CLI stops before running the tool
            yield ResultMessage("error_during_execution", 1, 1, True, 1, "fake-session", TURN_1)
            return
        yield UserMessage([ToolResultBlock("call-1", "README.md\ncalc", False)])
        yield _text(ANSWER)
        yield AssistantMessage([TextBlock(ANSWER)], model)
        yield _stream({"type": "message_delta", "usage": TURN_2})
        usage = _total(TURN_1, TURN_2)
        yield ResultMessage("success", 1, 1, False, 2, "fake-session", usage, ANSWER)
