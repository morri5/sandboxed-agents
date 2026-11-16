"""Bring a session back to a consistent state after the API process died.

`reconcile` runs inside the session's runner task before it takes new messages,
so the runner stays the session's only writer. From the folded log it:

1. reattaches the VM by name, or appends `sandbox_lost` (the next run recreates
   it from the repo plus the last saved workspace patch);
2. closes a run that was `running` when the API died. A `host` harness died with
   the API: `run_interrupted`. A `sandbox` harness may have kept working: every
   durable event in `runs/<run_id>.jsonl` that the log lacks is appended (the
   idem_key `<run_id>:<rseq>` makes this safe to repeat); if the runner job is
   still alive its output is followed until it exits; a run that still has no
   terminal event is `run_interrupted`.

The runner then re-runs the last user message if `needs_run` says so.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterable
from uuid import UUID

import asyncpg
from microsandbox import MicrosandboxError

from lab import config, events, sandbox
from lab.events import TERMINAL_RUN_EVENTS, AgentEvent, Event
from lab.harnesses import HARNESSES
from lab.state import OPEN, SessionState, fold

MAX_ATTEMPTS = 2  # runs per user message: the original plus one re-run after an interruption


def needs_run(state: SessionState) -> bool:
    """Whether the last user message should be run (again) now.

    A message that never started a run is run. An interrupted run is re-run once,
    unless it already answered: a second answer to one message is worse than none.
    """
    if state.session not in OPEN:
        return False
    if state.run == "queued":
        return True
    return state.run == "interrupted" and not state.answered and state.attempts < MAX_ATTEMPTS


def ingested_rseq(log: list[Event], run_id: str) -> int:
    """The highest runner counter of `run_id` already in the log."""
    prefix = f"{run_id}:"
    keys = [e.idem_key for e in log if e.idem_key and e.idem_key.startswith(prefix)]
    return max((int(key[len(prefix) :]) for key in keys), default=0)


async def ingest(
    pool: asyncpg.Pool, session_id: UUID, source: AsyncIterable[AgentEvent], after_rseq: int = 0
) -> bool:
    """Append the durable events from `source` past `after_rseq`; True if one ends the run.

    Live deltas are skipped: their moment has passed. Appending an event that is
    already in the log is a no-op (same idem_key), so overlapping sources are fine.
    """
    terminal = False
    async for ev in source:
        if ev.rseq is None:
            continue
        if ev.rseq > after_rseq:
            await events.append(pool, session_id, ev.type, ev.payload, f"{ev.run_id}:{ev.rseq}")
        terminal = terminal or ev.type in TERMINAL_RUN_EVENTS
    return terminal


async def _append(pool: asyncpg.Pool, session_id: UUID, type: str, **payload: object) -> int:
    return await events.append(pool, session_id, type, payload)


async def reconcile(pool: asyncpg.Pool, session_id: UUID) -> None:
    """Steps 1 and 2 of the module docstring; the caller decides on a re-run."""
    log = await events.read(pool, session_id)
    state = fold(log)
    if state.session not in (*OPEN, "running"):
        return
    name = state.sandbox_name
    sb = None
    if state.sandbox == "attached":
        assert name is not None
        sb = await sandbox.attach(name)
        if sb is None:
            await _append(pool, session_id, "sandbox_lost", name=name, reason="not_running")
    elif state.sandbox == "provisioning":
        # The API died while the VM was being set up: it may lack the repo, so it goes.
        await sandbox.destroy(session_id)
        reason = "provisioning_interrupted"
        await _append(pool, session_id, "sandbox_lost", name=name, reason=reason)
    if state.run != "running":
        return
    run_id = state.run_id
    assert run_id is not None and state.run_harness is not None
    terminal = False
    reason = "api_restart"  # a host harness ran in the API process and died with it
    if HARNESSES[state.run_harness].placement == "sandbox":
        reason = "sandbox_lost"
        if sb is not None:
            reason = "runner_lost"
            source = sandbox.run_file_events(sb, run_id)
            terminal = await ingest(pool, session_id, source, ingested_rseq(log, run_id))
            job = None if terminal else await sandbox.runner_job(sb, run_id)
            if job is not None:
                after = ingested_rseq(await events.read(pool, session_id), run_id)
                try:
                    async with asyncio.timeout(config.RUN_TIMEOUT_S):
                        terminal = await ingest(pool, session_id, sandbox.job_events(job), after)
                except TimeoutError:
                    with contextlib.suppress(MicrosandboxError):
                        await job.kill()
                    await _append(
                        pool,
                        session_id,
                        "run_failed",
                        run_id=run_id,
                        reason="timeout",
                        limit_s=config.RUN_TIMEOUT_S,
                    )
                    return
    if not terminal:
        await _append(pool, session_id, "run_interrupted", run_id=run_id, reason=reason)
