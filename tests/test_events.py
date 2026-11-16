import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from lab import events
from lab.events import ConcurrentWriterError, Event, Message


async def test_append_is_gapless(pool: asyncpg.Pool) -> None:
    sid = uuid.uuid4()
    seqs = [await events.append(pool, sid, "user_message", {"text": str(i)}) for i in range(20)]
    assert seqs == list(range(1, 21))
    other = uuid.uuid4()
    assert await events.append(pool, other, "user_message", {"text": "x"}) == 1
    assert [e.seq for e in await events.read(pool, sid)] == list(range(1, 21))
    assert [e.seq for e in await events.read(pool, sid, after_seq=15)] == [16, 17, 18, 19, 20]


async def test_concurrent_appends_stay_gapless(pool: asyncpg.Pool) -> None:
    sid = uuid.uuid4()
    results = await asyncio.gather(
        *(events.append(pool, sid, "tool_call", {"i": i}) for i in range(30)),
        return_exceptions=True,
    )
    written = sorted(r for r in results if isinstance(r, int))
    assert written == list(range(1, len(written) + 1))
    assert all(isinstance(r, int | ConcurrentWriterError) for r in results)
    assert [e.seq for e in await events.read(pool, sid)] == written


async def test_duplicate_idem_key_returns_same_seq(pool: asyncpg.Pool) -> None:
    sid = uuid.uuid4()
    first = await events.append(pool, sid, "user_message", {"text": "hi"}, idem_key="k1")
    await events.append(pool, sid, "agent_message", {"text": "hello"})
    again = await events.append(pool, sid, "user_message", {"text": "hi"}, idem_key="k1")
    assert first == again == 1
    assert len(await events.read(pool, sid)) == 2
    # The same key in another session is a different action.
    assert await events.append(pool, uuid.uuid4(), "user_message", {}, idem_key="k1") == 1


async def test_second_writer_fails_instead_of_waiting(pool: asyncpg.Pool) -> None:
    sid = uuid.uuid4()
    async with pool.acquire() as holder, holder.transaction():
        await holder.execute("SELECT pg_advisory_xact_lock($1)", events.lock_key(sid))
        with pytest.raises(ConcurrentWriterError):
            await asyncio.wait_for(events.append(pool, sid, "user_message", {}), timeout=2)
    assert await events.append(pool, sid, "user_message", {}) == 1


def _event(seq: int, type: str, **payload: object) -> Event:
    sid = uuid.UUID(int=1)
    return Event(sid, seq, type, dict(payload), None, datetime.now(UTC))


def test_fold_state() -> None:
    log = [
        _event(1, "session_created", repo_url="https://example.org/r.git", harness="scripted"),
        _event(2, "user_message", text="hi"),
        _event(3, "run_started", run_id="r1", harness="scripted"),
        _event(4, "sandbox_attached", name="lab-x"),
        _event(5, "tool_call", id="c1", name="shell", args={"command": "ls"}),
    ]
    state = events.fold_state(log)
    assert (state.status, state.run_id, state.sandbox, state.last_seq) == (
        "running",
        "r1",
        "lab-x",
        5,
    )
    log += [_event(6, "agent_message", text="hello"), _event(7, "run_finished")]
    state = events.fold_state(log)
    assert (state.status, state.run_id, state.harness) == ("idle", None, "scripted")
    assert state.transcript == [Message("user", "hi"), Message("assistant", "hello")]
    assert events.fold_state(log) == state  # deterministic
