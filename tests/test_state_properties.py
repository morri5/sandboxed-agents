"""Hypothesis: random messages, agent steps, API crashes and restarts, on both placements.

The log is the real one (Postgres through `events.append`), and recovery is the
real `recovery.reconcile` and `needs_run`. Only the VM is faked: a list per run
stands in for `runs/<run_id>.jsonl`, plus a flag for whether the runner job is
alive. The live session runner is modelled in a few lines that mirror
`SessionRunner` (append the message, start a run, append each step).

Invariants: never two running runs, seq stays gapless, every user message is
answered at most once, the fold is deterministic, and (in-VM harness) every event
the runner wrote reaches the log exactly once.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncpg
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from lab import events, recovery, sandbox
from lab.db import create_pool
from lab.events import TERMINAL_RUN_EVENTS, AgentEvent
from lab.state import OPEN, fold

SCRIPT = [
    ("tool_call", {"id": "c1"}),
    ("tool_result", {"id": "c1"}),
    ("tool_call", {"id": "c2"}),
    ("tool_result", {"id": "c2"}),
    ("agent_message", {"text": "done"}),
    ("run_finished", {}),
]
OPS = ["message", "retry", "step", "step", "step", "step", "crash", "restart", "runner_dies"]
HARNESS = {"host": "deepagents_ollama", "sandbox": "scripted"}


class World:
    """One session, the API process (up or down) and the fake VM."""

    def __init__(self, pool: asyncpg.Pool, placement: str) -> None:
        self.pool = pool
        self.placement = placement
        self.sid = uuid.uuid4()
        self.api_up = True
        self.active: str | None = None  # run being worked on (host: by the API; sandbox: in VM)
        self.runner_alive = False
        self.files: dict[str, list[AgentEvent]] = {}
        self.last_key: str | None = None

    async def append(self, type: str, payload: dict[str, Any], key: str | None = None) -> int:
        return await events.append(self.pool, self.sid, type, payload, key)

    async def state(self) -> Any:
        return fold(await events.read(self.pool, self.sid))

    # The live session runner -------------------------------------------------

    async def message(self, retry: bool) -> None:
        if not self.api_up or self.active is not None:
            return  # a real message would wait in the runner's queue
        if retry and self.last_key is None:
            return
        before = await self.state()
        if before.session not in OPEN:
            return
        key = self.last_key if retry else uuid.uuid4().hex
        self.last_key = key
        seq = await self.append("user_message", {"text": "hi"}, key)
        if seq > before.last_seq:
            await self.start_run()
        else:
            assert retry and (await self.state()).last_seq == before.last_seq

    async def start_run(self) -> None:
        state = await self.state()
        run_id = uuid.uuid4().hex
        payload = {"run_id": run_id, "harness": HARNESS[self.placement]}
        await self.append("run_started", {**payload, "message_seq": state.message_seq})
        if state.sandbox != "attached":
            await self.append("sandbox_provisioning", {"name": "vm"})
            await self.append("sandbox_attached", {"name": "vm", "commit": None})
        self.active = run_id
        self.files[run_id] = []
        self.runner_alive = self.placement == "sandbox"

    def produce(self, run_id: str) -> AgentEvent:
        """The in-VM runner writes its next event to the file (and stdout)."""
        file = self.files[run_id]
        type, payload = SCRIPT[len(file)]
        ev = AgentEvent(type, payload, run_id, len(file) + 1)
        file.append(ev)
        if type in TERMINAL_RUN_EVENTS:
            self.runner_alive = False
        return ev

    async def step(self) -> None:
        run_id = self.active
        if run_id is None:
            return
        if self.placement == "host":
            if not self.api_up:
                return
            rseq = len(self.files[run_id]) + 1
            type, payload = SCRIPT[rseq - 1]
            self.files[run_id].append(AgentEvent(type, payload, run_id, rseq))
            await self.append(type, payload, f"{run_id}:{rseq}")
            if type in TERMINAL_RUN_EVENTS:
                self.active = None
            return
        if not self.runner_alive:
            return
        ev = self.produce(run_id)
        if self.api_up:  # the API follows the runner's output
            await self.append(ev.type, ev.payload, f"{run_id}:{ev.rseq}")
            if ev.type in TERMINAL_RUN_EVENTS:
                self.active = None

    async def crash(self) -> None:
        self.api_up = False
        if self.placement == "host":
            self.active = None  # the loop lived in the API process
        # an in-VM runner keeps going

    async def runner_dies(self) -> None:
        if self.placement != "sandbox" or not self.runner_alive:
            return
        self.runner_alive = False
        if self.api_up and self.active is not None:
            # The API sees the runner exit without a terminal event (SandboxError).
            await self.append("run_failed", {"run_id": self.active, "reason": "error"})
            self.active = None

    async def restart(self) -> None:
        if self.api_up:
            return
        self.api_up = True
        await recovery.reconcile(self.pool, self.sid)
        if self.placement == "sandbox":
            self.active = None  # reconcile followed a live runner to its end
        if recovery.needs_run(await self.state()):
            await self.start_run()

    async def drain(self) -> None:
        await self.restart()
        while self.active is not None:
            await self.step()

    # The fake VM, as `recovery` sees it through `lab.sandbox` ------------------

    async def fake_attach(self, name: str) -> object:
        return self

    async def fake_destroy(self, session_id: uuid.UUID) -> None:
        pass

    async def run_file_events(self, sb: object, run_id: str) -> AsyncIterator[AgentEvent]:
        for ev in list(self.files.get(run_id, [])):
            yield ev

    async def runner_job(self, sb: object, run_id: str) -> str | None:
        return run_id if self.runner_alive and self.active == run_id else None

    async def job_events(self, job: str) -> AsyncIterator[AgentEvent]:
        while self.runner_alive:  # following the job: the runner works to its end
            self.produce(job)
        for ev in list(self.files[job]):  # job output replays from the start
            yield ev


@pytest.fixture
def db(test_dsn: str) -> Iterator[tuple[asyncio.Runner, asyncpg.Pool]]:
    with asyncio.Runner() as runner:
        pool = runner.run(create_pool(test_dsn))
        yield runner, pool
        runner.run(pool.close())


CURRENT: dict[str, World] = {}


@pytest.fixture
def fake_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    def route(method: str) -> Any:
        return lambda *args: getattr(CURRENT["world"], method)(*args)

    monkeypatch.setattr(sandbox, "attach", route("fake_attach"))
    monkeypatch.setattr(sandbox, "destroy", route("fake_destroy"))
    for name in ("run_file_events", "runner_job", "job_events"):
        monkeypatch.setattr(sandbox, name, route(name))


async def simulate(world: World, ops: list[str]) -> None:
    await world.append("session_created", {"repo_url": "x", "harness": HARNESS[world.placement]})
    for op in ops:
        match op:
            case "message" | "retry":
                await world.message(retry=op == "retry")
            case _:
                await getattr(world, op)()
        check_invariants(await events.read(world.pool, world.sid))
    await world.drain()
    log = await events.read(world.pool, world.sid)
    check_invariants(log)

    state = fold(log)
    assert state.run not in ("queued", "running")
    assert not recovery.needs_run(state)
    if world.placement == "sandbox":  # nothing the runner wrote is lost or doubled
        written = {f"{ev.run_id}:{ev.rseq}" for file in world.files.values() for ev in file}
        keys = Counter(e.idem_key for e in log if e.idem_key and ":" in e.idem_key)
        assert written == set(keys)
        assert all(n == 1 for n in keys.values())


def check_invariants(log: list[events.Event]) -> None:
    assert [e.seq for e in log] == list(range(1, len(log) + 1))
    running = 0
    answers = 0
    for e in log:
        if e.type == "run_started":
            running += 1
        elif e.type in TERMINAL_RUN_EVENTS:
            running -= 1
        elif e.type == "user_message":
            answers = 0
        elif e.type == "agent_message":
            answers += 1
        assert 0 <= running <= 1, "two runs at once"
        assert answers <= 1, "a message answered twice"
    assert fold(log) == fold(list(log))  # deterministic, and legal for every machine


@settings(
    max_examples=1000,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    placement=st.sampled_from(["host", "sandbox"]),
    ops=st.lists(st.sampled_from(OPS), min_size=5, max_size=60),
)
# The crash that matters most: after the answer, before `run_finished`.
@example(placement="host", ops=["message", *["step"] * 5, "crash", "restart"])
# An in-VM runner that wrote events while the API was down, then died.
@example(placement="sandbox", ops=["message", "step", "crash", "step", "runner_dies", "restart"])
def test_crashes_and_restarts_keep_the_invariants(
    db: tuple[asyncio.Runner, asyncpg.Pool], fake_vm: None, placement: str, ops: list[str]
) -> None:
    runner, pool = db
    CURRENT["world"] = world = World(pool, placement)
    runner.run(simulate(world, ops))
