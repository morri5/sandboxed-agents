import asyncio
import uuid

import asyncpg
import pytest

from lab import events
from lab.events import ConcurrentWriterError


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


async def test_fork_copies_the_prefix_atomically(pool: asyncpg.Pool) -> None:
    source, target = uuid.uuid4(), uuid.uuid4()
    for i in range(5):
        await events.append(pool, source, "user_message", {"text": str(i)}, idem_key=f"k{i}")
    assert await events.fork(pool, source, target, 3) == 4
    copied = await events.read(pool, target)
    assert [(e.seq, e.type, e.payload, e.idem_key) for e in copied[:3]] == [
        (e.seq, e.type, e.payload, e.idem_key) for e in (await events.read(pool, source))[:3]
    ]
    assert copied[3].type == "forked_from"
    assert copied[3].payload == {"session_id": str(source), "at_seq": 3}
    assert await events.append(pool, target, "user_message", {"text": "x"}) == 5
    assert len(await events.read(pool, source)) == 5  # the source is untouched


async def test_open_sessions(pool: asyncpg.Pool) -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for sid in (a, b, c):
        await events.append(pool, sid, "session_created", {})
    await events.append(pool, b, "session_closed", {})
    await events.append(pool, c, "session_failed", {})
    assert await events.open_sessions(pool) == [a]
