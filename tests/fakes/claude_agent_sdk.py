"""A stand-in for `claude_agent_sdk`, for tests without a VM or a model.

The contract tests copy this file into a temp dir as `claude_agent_sdk.py` and put
that dir on the runner's PYTHONPATH. The client replays one short recorded-shape
run: a turn that lists the repo with Bash, then a turn that answers. With subagents
in its options (`claude_factory`), the first turn calls the `Agent` tool instead,
and the subagent makes one Grep call of its own (`parent_tool_use_id` set). Like the
CLI, the fake logs the subagent's model call (`SUB_CALL`) only in a transcript under
`~/.claude/projects/` (one line per content block, so twice), not in the stream
events or the result totals. The
client writes its options to `fake_sdk_options.json` in the working directory, so
a test can check them. The message
shapes follow what the real SDK (0.2.182) produced with DeepSeek: one
`AssistantMessage` per content block, per-turn usage in the `message_delta`
stream event, totals in the `ResultMessage`.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TURN_1 = {"input_tokens": 40_000, "cache_read_input_tokens": 0, "output_tokens": 100}
TURN_2 = {"input_tokens": 200, "cache_read_input_tokens": 40_000, "output_tokens": 20}
SUB_CALL = {"input_tokens": 20_000, "cache_read_input_tokens": 0, "output_tokens": 50}
ANSWER = "The repo has two entries."
OPTIONS_FILE = "fake_sdk_options.json"


@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None


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
    parent_tool_use_id: str | None = None
    usage: dict[str, Any] | None = None
    message_id: str | None = None
    uuid: str | None = None


@dataclass
class UserMessage:
    content: str | list[Any]
    parent_tool_use_id: str | None = None


@dataclass
class StreamEvent:
    uuid: str
    session_id: str
    event: dict[str, Any]
    parent_tool_use_id: str | None = None


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
        options = {"model": self.options.model, **self.options.extra}
        Path(OPTIONS_FILE).write_text(
            json.dumps(
                options, default=lambda o: asdict(o) if isinstance(o, AgentDefinition) else str(o)
            )
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def interrupt(self) -> None:
        self.interrupted = True

    async def receive_response(self) -> AsyncIterator[Any]:
        model = self.options.model or "fake"
        subagents = bool(self.options.extra.get("agents"))
        call = (
            ToolUseBlock("call-1", "Agent", {"subagent_type": "locator", "prompt": "list it"})
            if subagents
            else ToolUseBlock("call-1", "Bash", {"command": "ls"})
        )
        turn_1 = [
            _text("Let "),
            _text("me look."),
            AssistantMessage([TextBlock("Let me look.")], model),
            AssistantMessage([call], model),
            _stream({"type": "message_delta", "usage": TURN_1}),
        ]
        interrupted = ResultMessage("error_during_execution", 1, 1, True, 1, "fake-session", TURN_1)
        for item in turn_1:
            yield item
        if self.interrupted:  # the CLI stops before running the tool
            yield interrupted
            return
        if subagents:
            _write_subagent_transcript()
            yield AssistantMessage([TextBlock("Searching.")], model, parent_tool_use_id="call-1")
            if self.interrupted:
                yield interrupted
                return
            grep = ToolUseBlock("sub-1", "Grep", {"pattern": "def "})
            yield AssistantMessage([grep], model, parent_tool_use_id="call-1")
            if self.interrupted:
                yield interrupted
                return
            found = ToolResultBlock("sub-1", "calc/__init__.py", False)
            yield UserMessage([found], parent_tool_use_id="call-1")
        yield UserMessage([ToolResultBlock("call-1", "README.md\ncalc", False)])
        yield _text(ANSWER)
        yield AssistantMessage([TextBlock(ANSWER)], model)
        yield _stream({"type": "message_delta", "usage": TURN_2})
        usage = _total(TURN_1, TURN_2)
        yield ResultMessage("success", 1, 1, False, 2, "fake-session", usage, ANSWER)


def _write_subagent_transcript() -> None:
    """The subagent's one model call, as the CLI logs it: one line per content block,
    the first before the output tokens are known."""
    directory = Path.home() / ".claude" / "projects" / "-repo" / "fake-session" / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    early = {**SUB_CALL, "output_tokens": 0}
    lines = [
        {"type": "user", "message": {"role": "user", "content": "list it"}},
        {"type": "assistant", "message": {"id": "msg-sub-1", "usage": early}},
        {"type": "assistant", "message": {"id": "msg-sub-1", "usage": SUB_CALL}},
    ]
    (directory / "agent-1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
