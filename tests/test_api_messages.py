"""Message options through the real API and session runner, with a recording harness
and a fake VM: `fresh_context` and `limits` are stored in `user_message`, bounded,
and applied to the run."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import asyncpg
import httpx
import pytest
from microsandbox import Sandbox

from lab import events, sandbox
from lab.api import app
from lab.events import AgentEvent, Limits, Message
from lab.fanout import FanOut
from lab.harnesses import HARNESSES
from lab.sandbox import VmSpec
from lab.session import SessionRunner
from lab.state import fold


class RecordingHarness:
    """Answers at once (or after 5 s for "slow") and records what each run got."""

    name = "recording"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"native_resume", "limits"})
    model = "none"

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def vm_spec(self) -> VmSpec:
        return VmSpec()

    async def run(
        self,
        transcript: list[Message],
        sandbox: Sandbox,
        run_id: str,
        resume: str | None = None,
        limits: Limits | None = None,
    ) -> AsyncIterator[AgentEvent]:
        texts = [m.text for m in transcript]
        self.runs.append({"transcript": texts, "resume": resume, "limits": limits})
        if texts[-1] == "slow":
            await asyncio.sleep(5)
        yield AgentEvent("agent_message", {"text": "ok"}, run_id, 1)
        yield AgentEvent("run_finished", {"resume": f"sdk-{len(self.runs)}"}, run_id, 2)


@pytest.fixture
async def client(
    pool: asyncpg.Pool, test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, RecordingHarness]]:
    """The API without its lifespan (no recovery of other sessions), on the test
    database, with every VM operation faked: one VM per session that stays up."""
    harness = RecordingHarness()
    monkeypatch.setitem(HARNESSES, harness.name, harness)
    vm = object()

    async def provision(*args: Any, **kwargs: Any) -> tuple[object, None]:
        return vm, None

    async def attach(name: str) -> object:
        return vm

    async def save_workspace(sb: object) -> bytes:
        return b""

    monkeypatch.setattr(sandbox, "provision", provision)
    monkeypatch.setattr(sandbox, "attach", attach)
    monkeypatch.setattr(sandbox, "save_workspace", save_workspace)
    fanout = FanOut(pool, test_dsn)
    await fanout.start()
    app.state.pool, app.state.fanout = pool, fanout
    runners: dict[uuid.UUID, SessionRunner] = {}
    app.state.runners = runners
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as c:
        yield c, harness
    for runner in runners.values():
        await runner.stop()
    await fanout.close()


async def _say(c: httpx.AsyncClient, session_id: str, text: str, **options: Any) -> None:
    """Send a message and wait for its run to end."""
    body = {"text": text, "idem_key": str(uuid.uuid4()), **options}
    assert (await c.post(f"/sessions/{session_id}/messages", json=body)).status_code == 202
    async with asyncio.timeout(15):
        while True:
            await asyncio.sleep(0.05)
            state = fold(await events.read(app.state.pool, uuid.UUID(session_id)))
            if (
                any(m.role == "user" and m.text == text for m in state.transcript)
                and state.session == "idle"
                and state.run in ("finished", "failed")
            ):
                return


async def _session(c: httpx.AsyncClient) -> str:
    r = await c.post("/sessions", json={"repo_url": "fixtures/calc-repo", "harness": "recording"})
    assert r.status_code == 201
    session_id: str = r.json()["session_id"]
    return session_id


async def test_fresh_context_drops_the_conversation_and_the_resume(
    client: tuple[httpx.AsyncClient, RecordingHarness],
) -> None:
    c, harness = client
    session_id = await _session(c)
    await _say(c, session_id, "first")
    await _say(c, session_id, "second")
    limits: dict[str, Any] = {"max_tool_calls": 300, "run_timeout_s": 1800, "max_usd": 1.0}
    await _say(c, session_id, "third", fresh_context=True, limits=limits)

    first, second, third = harness.runs
    assert first == {"transcript": ["first"], "resume": None, "limits": None}
    # Same VM: the harness continues its own session, on the whole conversation.
    assert second["transcript"] == ["first", "ok", "second"]
    assert second["resume"] == "sdk-1"
    # Same VM again, but a fresh context: no resume, only the new message.
    assert third == {"transcript": ["third"], "resume": None, "limits": Limits(**limits)}

    log = await events.read(app.state.pool, uuid.UUID(session_id))
    messages = [e.payload for e in log if e.type == "user_message"]
    assert messages == [
        {"text": "first"},
        {"text": "second"},
        {"text": "third", "fresh_context": True, "limits": limits},
    ]
    assert [e.type for e in log].count("sandbox_attached") == 1


async def test_run_timeout_from_the_message(
    client: tuple[httpx.AsyncClient, RecordingHarness],
) -> None:
    c, _ = client
    session_id = await _session(c)
    await _say(c, session_id, "slow", limits={"run_timeout_s": 1})
    log = await events.read(app.state.pool, uuid.UUID(session_id))
    failed = next(e.payload for e in log if e.type == "run_failed")
    assert failed["reason"] == "timeout" and failed["limit_s"] == 1


@pytest.mark.parametrize(
    "limits",
    [
        {"max_tool_calls": 1001},
        {"max_tool_calls": 0},
        {"run_timeout_s": 7201},
        {"max_usd": 20.01},
        {"max_usd": 0},
    ],
)
async def test_out_of_bounds_limits_are_refused(
    limits: dict[str, Any], client: tuple[httpx.AsyncClient, RecordingHarness]
) -> None:
    c, _ = client
    session_id = await _session(c)
    body = {"text": "hi", "idem_key": "k", "limits": limits}
    r = await c.post(f"/sessions/{session_id}/messages", json=body)
    assert r.status_code == 422, r.text
    log = await events.read(app.state.pool, uuid.UUID(session_id))
    assert "user_message" not in [e.type for e in log]


async def _created(c: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    """The `session_created` payload of a new session made with `body`."""
    r = await c.post("/sessions", json={"repo_url": "fixtures/calc-repo", **body})
    assert r.status_code == 201, r.text
    log = await events.read(app.state.pool, uuid.UUID(r.json()["session_id"]))
    return log[0].payload


async def test_seed_files_are_kept_in_session_created(
    client: tuple[httpx.AsyncClient, RecordingHarness],
) -> None:
    c, _ = client
    seed = {"thoughts/shared/factory/plans/2026-11-20-x.md": "# Plan\n"}
    assert (await _created(c, seed_files=seed))["seed_files"] == seed
    # Without a seed the payload stays as it was.
    assert await _created(c) == {"repo_url": "fixtures/calc-repo", "harness": "scripted"}
    assert await _created(c, seed_files={}) == await _created(c)


@pytest.mark.parametrize(
    "seed",
    [
        {"/etc/passwd": "x"},
        {"thoughts/../../etc/x": "x"},
        {"..": "x"},
        {".git/info/exclude": "x"},
        {".git": "x"},
        {"a b.md": "x"},
        {"a/*.md": "x"},
        {"a//b.md": "x"},
        {"a/": "x"},
        {"": "x"},
        {"a.md": "x" * (1 << 20), "b.md": "y"},
    ],
)
async def test_bad_seed_files_are_refused(
    seed: dict[str, str], client: tuple[httpx.AsyncClient, RecordingHarness]
) -> None:
    c, _ = client
    r = await c.post("/sessions", json={"repo_url": "fixtures/calc-repo", "seed_files": seed})
    assert r.status_code == 422, r.text
