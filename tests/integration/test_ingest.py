"""Reading a runner's event file into the log is idempotent.

A real runner writes `runs/<run_id>.jsonl` in a real VM; ingesting that file
twice appends its events once.
"""

import uuid

import asyncpg
import pytest

from lab import events, sandbox
from lab.recovery import ingest

pytestmark = pytest.mark.integration


async def test_ingesting_the_same_file_twice_appends_nothing(
    pool: asyncpg.Pool, calc_repo: str
) -> None:
    session_id = uuid.uuid4()
    run_id = uuid.uuid4().hex
    vm, _ = await sandbox.provision(sandbox.sandbox_name(session_id), calc_repo, "sandbox")
    try:
        task = {"run_id": run_id, "harness": "scripted", "transcript": []}
        produced = [ev async for ev in sandbox.exec_runner(vm, task) if ev.rseq is not None]

        assert await ingest(pool, session_id, sandbox.run_file_events(vm, run_id))
        first = await events.read(pool, session_id)
        assert [e.idem_key for e in first] == [f"{run_id}:{ev.rseq}" for ev in produced]
        assert first[-1].type == "run_finished"

        assert await ingest(pool, session_id, sandbox.run_file_events(vm, run_id))
        assert await events.read(pool, session_id) == first
    finally:
        await sandbox.destroy(session_id)
