"""Live fan-out of session events to in-process subscribers (WebSockets).

One dedicated asyncpg connection LISTENs on the `events` channel. A notification
only carries `{session_id, seq}` and works as a wake-up: the subscriber then
reads every event after the last seq it delivered. That makes delivery gapless,
in order and exactly once, even when notifications are lost. After the listener
reconnects, every subscriber is woken up to run the same catch-up.

`agent_delta` tokens never touch Postgres: `publish_delta` hands them straight to
the session's subscribers.
"""

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import asyncpg

from lab.events import CHANNEL, read

log = logging.getLogger(__name__)

_WAKE: dict[str, Any] = {"kind": "wake"}


class FanOut:
    def __init__(self, pool: asyncpg.Pool, dsn: str) -> None:
        self._pool = pool
        self._dsn = dsn
        self._subscribers: dict[UUID, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._conn: Any = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        await self._connect()

    async def close(self) -> None:
        self._closed = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._conn is not None and not self._conn.is_closed():
            await self._conn.close()

    @property
    def listener_pid(self) -> int:
        """Backend pid of the LISTEN connection (tests terminate it to force a reconnect)."""
        pid: int = self._conn.get_server_pid()
        return pid

    async def _connect(self) -> None:
        conn = await asyncpg.connect(self._dsn)
        conn.add_termination_listener(self._on_terminated)
        await conn.add_listener(CHANNEL, self._on_notify)
        self._conn = conn

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self._wake(UUID(json.loads(payload)["session_id"]))

    def _on_terminated(self, _conn: Any) -> None:
        if not self._closed:
            self._reconnect_task = asyncio.get_running_loop().create_task(self._reconnect())

    async def _reconnect(self) -> None:
        delay = 0.1
        while not self._closed:
            try:
                await self._connect()
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("fan-out listener reconnect failed: %r", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue
            log.info("fan-out listener reconnected")
            for session_id in list(self._subscribers):
                self._wake(session_id)  # notifications sent while down are lost: catch up
            return

    def _wake(self, session_id: UUID) -> None:
        for queue in self._subscribers.get(session_id, ()):
            queue.put_nowait(_WAKE)

    def publish_delta(self, session_id: UUID, payload: dict[str, Any]) -> None:
        for queue in self._subscribers.get(session_id, ()):
            queue.put_nowait({"kind": "delta", "payload": payload})

    async def subscribe(self, session_id: UUID, after: int = 0) -> AsyncGenerator[dict[str, Any]]:
        """Yield `{"kind": "event", ...}` for every durable event after `after`, then live.

        Live deltas arrive as `{"kind": "delta", "payload": ...}`. The subscriber
        registers before its first read, so nothing appended in between is missed.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[session_id].add(queue)
        last = after
        try:
            item = _WAKE  # the initial catch-up
            while True:
                if item is _WAKE:
                    for event in await read(self._pool, session_id, last):
                        last = event.seq
                        yield {"kind": "event", **event.to_json()}
                else:
                    yield item
                item = await queue.get()
        finally:
            subscribers = self._subscribers[session_id]
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[session_id]
