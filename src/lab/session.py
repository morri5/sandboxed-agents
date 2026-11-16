"""The per-session runner task: the only writer of a session's events once it exists.

The API hands user messages to the session's runner through a queue. The runner
appends the `user_message` (idempotent on the client's `idem_key`), runs the
harness and appends what it produces. Live `agent_delta` tokens go to the fan-out
only.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from microsandbox import Sandbox

from lab import events
from lab.events import TERMINAL_RUN_EVENTS
from lab.fanout import FanOut
from lab.harnesses import HARNESSES
from lab.sandbox import ensure_sandbox, sandbox_name

log = logging.getLogger(__name__)


class SessionRunner:
    def __init__(self, pool: asyncpg.Pool, fanout: FanOut, session_id: UUID) -> None:
        self.pool = pool
        self.fanout = fanout
        self.session_id = session_id
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.sandbox: Sandbox | None = None
        self.task = asyncio.create_task(self._loop(), name=f"session-{session_id}")

    def submit(self, text: str, idem_key: str) -> None:
        self.queue.put_nowait((text, idem_key))

    async def stop(self) -> None:
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            text, idem_key = await self.queue.get()
            try:
                await self._handle(text, idem_key)
            except Exception:
                log.exception("session %s: message %s failed", self.session_id, idem_key)

    async def _append(self, type: str, payload: dict[str, Any], idem_key: str | None = None) -> int:
        return await events.append(self.pool, self.session_id, type, payload, idem_key)

    async def _handle(self, text: str, idem_key: str) -> None:
        state = events.fold_state(await events.read(self.pool, self.session_id))
        seq = await self._append("user_message", {"text": text}, idem_key)
        if seq <= state.last_seq:
            return  # a retry of a message that is already in the log
        assert state.harness is not None and state.repo_url is not None
        harness = HARNESSES[state.harness]
        run_id = uuid4().hex
        await self._append("run_started", {"run_id": run_id, "harness": harness.name})
        terminal = False
        try:
            if self.sandbox is None:
                self.sandbox = await ensure_sandbox(self.session_id, state.repo_url)
                if state.sandbox is None:
                    await self._append("sandbox_attached", {"name": sandbox_name(self.session_id)})
            transcript = [*state.transcript, events.Message("user", text)]
            async for ev in harness.run(transcript, self.sandbox, run_id):
                if ev.rseq is None:
                    self.fanout.publish_delta(self.session_id, {"type": ev.type, **ev.payload})
                    continue
                await self._append(ev.type, ev.payload, f"{run_id}:{ev.rseq}")
                terminal = terminal or ev.type in TERMINAL_RUN_EVENTS
            if not terminal:
                await self._append("run_failed", {"run_id": run_id, "reason": "no_terminal_event"})
        except Exception as exc:
            log.exception("session %s: run %s failed", self.session_id, run_id)
            if not terminal:
                await self._append(
                    "run_failed", {"run_id": run_id, "reason": "error", "error": repr(exc)}
                )
