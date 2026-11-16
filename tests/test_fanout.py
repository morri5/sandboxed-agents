import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from lab import events
from lab.fanout import FanOut


@pytest.fixture
async def fanout(pool: asyncpg.Pool, test_dsn: str) -> AsyncIterator[FanOut]:
    fo = FanOut(pool, test_dsn)
    await fo.start()
    yield fo
    await fo.close()


async def collect(stream: AsyncIterator[dict[str, Any]], until_seq: int) -> list[dict[str, Any]]:
    items = []
    async for item in stream:
        items.append(item)
        if item["kind"] == "event" and item["seq"] == until_seq:
            return items
    raise AssertionError("stream ended")


async def test_mid_stream_subscriber_gets_every_event_once_in_order(
    pool: asyncpg.Pool, fanout: FanOut
) -> None:
    sid = uuid.uuid4()
    for i in range(5):
        await events.append(pool, sid, "tool_call", {"i": i})
    subscriber = asyncio.create_task(collect(fanout.subscribe(sid, after=2), until_seq=40))
    for i in range(5, 40):
        await events.append(pool, sid, "tool_call", {"i": i})
        if i % 7 == 0:
            await asyncio.sleep(0)  # let the subscriber interleave with the writer
    items = await asyncio.wait_for(subscriber, timeout=10)
    assert [item["seq"] for item in items] == list(range(3, 41))


async def test_deltas_reach_live_subscribers(pool: asyncpg.Pool, fanout: FanOut) -> None:
    sid = uuid.uuid4()
    stream = fanout.subscribe(sid)
    await events.append(pool, sid, "run_started", {"run_id": "r"})
    first = await anext(stream)
    fanout.publish_delta(sid, {"type": "agent_delta", "text": "tok"})
    second = await anext(stream)
    assert first["seq"] == 1
    assert second == {"kind": "delta", "payload": {"type": "agent_delta", "text": "tok"}}
    assert len(await events.read(pool, sid)) == 1  # deltas never reach Postgres
    await stream.aclose()


async def test_catch_up_after_forced_listener_reconnect(
    pool: asyncpg.Pool, fanout: FanOut, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = uuid.uuid4()
    received: list[int] = []

    async def consume() -> None:
        async for item in fanout.subscribe(sid):
            received.append(item["seq"])

    async def wait_for(count: int) -> None:
        async with asyncio.timeout(10):
            while len(received) < count:
                await asyncio.sleep(0.02)

    consumer = asyncio.create_task(consume())
    for i in range(10):
        await events.append(pool, sid, "tool_call", {"i": i})
    await wait_for(10)

    # Hold the reconnect until released, so the notifications below are surely lost.
    gate = asyncio.Event()
    connect = fanout._connect

    async def gated_connect() -> None:
        await gate.wait()
        await connect()

    monkeypatch.setattr(fanout, "_connect", gated_connect)
    old_conn = fanout._conn
    await pool.execute("SELECT pg_terminate_backend($1)", fanout.listener_pid)
    async with asyncio.timeout(10):
        while not old_conn.is_closed():
            await asyncio.sleep(0.02)
    for i in range(10, 20):
        await events.append(pool, sid, "tool_call", {"i": i})
    await asyncio.sleep(0.3)
    assert received == list(range(1, 11))  # nothing arrives while the listener is down

    gate.set()
    await wait_for(20)
    await asyncio.sleep(0.1)  # no duplicates trailing behind
    assert received == list(range(1, 21))
    consumer.cancel()
