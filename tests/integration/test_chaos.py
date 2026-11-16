"""Chaos: `kill -9` the API in the middle of a run, restart it, check the session.

The API runs as its own process; the test reads the log straight from Postgres,
so it can watch while the API is down. Each test prints its recovery times
(`make test-chaos` shows them).
"""

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

import asyncpg
import httpx
import pytest

from lab import config, events, sandbox
from lab.recovery import needs_run
from lab.state import fold
from tests.integration.conftest import free_port, ollama_has_model, start_api, stop_api

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

STEP_DELAY_S = "2"  # slows the scripted agent down so the kill lands mid-run
RUN_TIMEOUT = 600


class Api:
    """The API process, which the test can kill and start again on the same port."""

    def __init__(self, dsn: str, **env: str) -> None:
        self.dsn = dsn
        self.env = env
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = start_api(dsn, self.port, **env)

    def kill(self) -> None:
        self.proc.kill()  # SIGKILL: no shutdown code runs
        self.proc.wait()

    def restart(self) -> float:
        started = time.perf_counter()
        self.proc = start_api(self.dsn, self.port, **self.env)
        return time.perf_counter() - started

    def new_session(self, repo: str, harness: str) -> uuid.UUID:
        body = {"repo_url": repo, "harness": harness}
        return uuid.UUID(httpx.post(f"{self.url}/sessions", json=body).json()["session_id"])

    def say(self, session_id: uuid.UUID, text: str) -> None:
        body = {"text": text, "idem_key": str(uuid.uuid4())}
        r = httpx.post(f"{self.url}/sessions/{session_id}/messages", json=body)
        assert r.status_code == 202


async def wait_for(
    pool: asyncpg.Pool, session_id: uuid.UUID, done: Callable[[list[events.Event]], bool]
) -> list[events.Event]:
    async with asyncio.timeout(RUN_TIMEOUT):
        while True:
            log = await events.read(pool, session_id)
            if done(log):
                return log
            await asyncio.sleep(0.1)


def settled(log: list[events.Event]) -> bool:
    """The session is idle and nothing is left to re-run."""
    state = fold(log)
    return state.session == "idle" and not needs_run(state)


def has(type: str) -> Callable[[list[events.Event]], bool]:
    return lambda log: any(e.type == type for e in log)


def run_keys(log: list[events.Event], run_id: str) -> Counter[str]:
    return Counter(e.idem_key for e in log if e.idem_key and e.idem_key.startswith(f"{run_id}:"))


async def file_events(session_id: uuid.UUID, run_id: str) -> list[events.AgentEvent]:
    vm = await sandbox.attach(sandbox.sandbox_name(session_id))
    assert vm is not None
    return [ev async for ev in sandbox.run_file_events(vm, run_id)]


def report(name: str, **numbers: Any) -> None:
    print(f"\nRECOVERY {name}: " + ", ".join(f"{k}={v}" for k, v in numbers.items()))


@pytest.fixture
async def api_ctl(test_dsn: str, pool: asyncpg.Pool) -> Any:
    """Starts APIs on demand; stops them and destroys their sessions' VMs at the end."""
    apis: list[Api] = []
    sessions: list[uuid.UUID] = []

    def start(**env: str) -> Api:
        api = Api(test_dsn, **env)
        apis.append(api)
        return api

    yield start, sessions
    for api in apis:
        if api.proc.poll() is None:
            stop_api(api.proc)
    for session_id in sessions:
        await sandbox.destroy(session_id)


async def test_sandbox_runner_keeps_working_while_the_api_is_down(
    api_ctl: Any, pool: asyncpg.Pool, calc_repo: str
) -> None:
    start, sessions = api_ctl
    api = start(LAB_SCRIPTED_STEP_DELAY_S=STEP_DELAY_S)
    sid = api.new_session(calc_repo, "scripted")
    sessions.append(sid)
    api.say(sid, "Do the tests pass?")
    log = await wait_for(pool, sid, has("tool_call"))
    run_id = fold(log).run_id
    assert run_id is not None

    api.kill()
    at_kill = run_keys(await events.read(pool, sid), run_id)
    await asyncio.sleep(5)  # the runner in the VM goes on without the API
    backlog = await file_events(sid, run_id)
    assert len(backlog) > sum(at_kill.values()), "nothing was written while the API was down"

    started = time.perf_counter()  # recovery times count from the restart
    restart_s = api.restart()
    in_file = {f"{run_id}:{ev.rseq}" for ev in backlog}
    await wait_for(pool, sid, lambda log: in_file <= set(run_keys(log, run_id)))
    caught_up_s = time.perf_counter() - started
    log = await wait_for(pool, sid, settled)

    types = [e.type for e in log]
    assert [e.seq for e in log] == list(range(1, len(log) + 1))
    assert types.count("run_started") == 1  # finished without being re-queued
    assert "run_interrupted" not in types
    assert types.count("run_finished") == types.count("agent_message") == 1
    everything = {f"{run_id}:{ev.rseq}" for ev in await file_events(sid, run_id)}
    keys = run_keys(log, run_id)
    assert set(keys) == everything and all(n == 1 for n in keys.values())  # once each
    report(
        "sandbox, runner alive",
        api_restart_s=round(restart_s, 2),
        backlog_events=len(backlog) - sum(at_kill.values()),
        backlog_ingested_s=round(caught_up_s, 2),
    )


async def test_sandbox_runner_dies_while_the_api_is_down(
    api_ctl: Any, pool: asyncpg.Pool, calc_repo: str
) -> None:
    start, sessions = api_ctl
    api = start(LAB_SCRIPTED_STEP_DELAY_S=STEP_DELAY_S)
    sid = api.new_session(calc_repo, "scripted")
    sessions.append(sid)
    api.say(sid, "Do the tests pass?")
    run_id = fold(await wait_for(pool, sid, has("tool_call"))).run_id
    assert run_id is not None

    api.kill()
    vm = await sandbox.attach(sandbox.sandbox_name(sid))
    assert vm is not None
    job = await sandbox.runner_job(vm, run_id)
    assert job is not None
    await job.kill()
    written = len(await file_events(sid, run_id))

    started = time.perf_counter()  # recovery times count from the restart
    restart_s = api.restart()
    await wait_for(pool, sid, has("run_interrupted"))
    interrupted_s = time.perf_counter() - started
    log = await wait_for(pool, sid, settled)

    types = [e.type for e in log]
    interrupted = next(e for e in log if e.type == "run_interrupted")
    assert interrupted.payload == {"run_id": run_id, "reason": "runner_lost"}
    assert sum(run_keys(log, run_id).values()) == written  # what it wrote before dying is kept
    assert types.count("run_started") == 2  # re-queued exactly once
    assert types.count("user_message") == 1
    assert types.count("run_finished") == types.count("agent_message") == 1
    report(
        "sandbox, runner dead",
        api_restart_s=round(restart_s, 2),
        run_interrupted_s=round(interrupted_s, 2),
    )


@pytest.mark.slow
@pytest.mark.skipif(not ollama_has_model(), reason=f"Ollama with {config.OLLAMA_MODEL} needed")
async def test_host_loop_dies_with_the_api(
    api_ctl: Any, pool: asyncpg.Pool, calc_repo: str
) -> None:
    start, sessions = api_ctl
    api = start()
    sid = api.new_session(calc_repo, "deepagents_ollama")
    sessions.append(sid)
    api.say(sid, "One test in this repo fails. Find the bug and fix the code so all tests pass.")
    await wait_for(pool, sid, has("tool_call"))

    api.kill()
    started = time.perf_counter()  # recovery times count from the restart
    restart_s = api.restart()
    await wait_for(pool, sid, has("run_interrupted"))
    interrupted_s = time.perf_counter() - started
    log = await wait_for(pool, sid, settled)

    types = [e.type for e in log]
    interrupted = next(e for e in log if e.type == "run_interrupted")
    assert interrupted.payload["reason"] == "api_restart"
    assert types.count("run_started") == 2  # re-queued exactly once
    assert types.count("user_message") == 1
    assert types.count("agent_message") == 1, [e.payload for e in log if e.type == "run_failed"]
    assert fold(log).run == "finished"
    report(
        "host",
        api_restart_s=round(restart_s, 2),
        run_interrupted_s=round(interrupted_s, 2),
    )
