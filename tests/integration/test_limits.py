"""Run limits: a model that never stops calling tools ends with `run_failed{reason}`.

The model is a fake that asks for `ls` forever, so the test is deterministic and
needs no Ollama; everything else is real (session runner, event log, VM).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import asyncpg
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from lab import config, events
from lab.fanout import FanOut
from lab.harnesses import HARNESSES
from lab.harnesses.deepagents_ollama import DeepAgentsOllamaHarness
from lab.sandbox import REPO_DIR, destroy
from lab.session import SessionRunner

pytestmark = pytest.mark.integration


class LoopingModel(BaseChatModel):
    """Answers every turn with one more `ls` tool call (and never streams tokens)."""

    turns: int = 0

    @property
    def _llm_type(self) -> str:
        return "looping"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "LoopingModel":
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> Any:
        self.turns += 1
        call = {"name": "ls", "args": {"path": REPO_DIR}, "id": f"call-{self.turns}"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])


def _terminal(log: list[events.Event]) -> events.Event:
    return next(e for e in log if e.type in events.TERMINAL_RUN_EVENTS)


@pytest.fixture
async def run_session(
    test_dsn: str, pool: asyncpg.Pool, calc_repo: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[[DeepAgentsOllamaHarness], Any]]:
    fanout = FanOut(pool, test_dsn)
    await fanout.start()
    sessions: list[uuid.UUID] = []

    async def run(harness: DeepAgentsOllamaHarness) -> list[events.Event]:
        monkeypatch.setitem(HARNESSES, "looping", harness)
        session_id = uuid.uuid4()
        sessions.append(session_id)
        await events.append(
            pool, session_id, "session_created", {"repo_url": calc_repo, "harness": "looping"}
        )
        runner = SessionRunner(pool, fanout, session_id)
        runner.submit("list the repo, forever", str(uuid.uuid4()))
        try:
            async with asyncio.timeout(120):
                while True:
                    log = await events.read(pool, session_id)
                    if any(e.type in events.TERMINAL_RUN_EVENTS for e in log):
                        # Cut at the run's end (the workspace snapshot may follow).
                        return [e for e in log if e.seq <= _terminal(log).seq]
                    await asyncio.sleep(0.2)
        finally:
            await runner.stop()

    yield run
    await fanout.close()
    for session_id in sessions:
        await destroy(session_id)


async def test_max_tool_calls(run_session: Callable[[DeepAgentsOllamaHarness], Any]) -> None:
    log = await run_session(DeepAgentsOllamaHarness(model=LoopingModel(), max_tool_calls=3))
    types = [e.type for e in log]
    assert types.count("tool_call") == 3
    assert types.count("tool_result") == 3  # the fourth call was never executed
    assert log[-1].type == "run_failed"
    assert log[-1].payload == {"reason": "max_tool_calls", "limit": 3}


async def test_run_timeout(
    run_session: Callable[[DeepAgentsOllamaHarness], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "RUN_TIMEOUT_S", 3)
    harness = DeepAgentsOllamaHarness(model=LoopingModel(), max_tool_calls=100_000)
    log = await run_session(harness)
    assert log[-1].type == "run_failed"
    assert log[-1].payload["reason"] == "timeout"
    assert [e.type for e in log].count("run_failed") == 1
