"""The per-session runner task: the only writer of a session's events once it exists.

The API hands user messages (and a close) to the session's runner through a
queue. The runner first reconciles the session with what survived a crash (see
`recovery.py`), then works through the queue: it appends the `user_message`
(idempotent on the client's `idem_key`), makes sure the session has a VM, runs
the harness and appends what it produces. Live `agent_delta` tokens go to the
fan-out only. After every run it saves the VM's workspace as a patch, so a lost
VM or a fork can be rebuilt.
"""

import asyncio
import base64
import logging
import time
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg
from microsandbox import Sandbox

from lab import config, events, recovery, sandbox
from lab.events import TERMINAL_RUN_EVENTS
from lab.fanout import FanOut
from lab.harnesses import HARNESSES
from lab.state import OPEN, SessionState, fold

log = logging.getLogger(__name__)


class SessionRunner:
    def __init__(self, pool: asyncpg.Pool, fanout: FanOut, session_id: UUID) -> None:
        self.pool = pool
        self.fanout = fanout
        self.session_id = session_id
        self.queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()  # None closes
        self.task = asyncio.create_task(self._loop(), name=f"session-{session_id}")

    def submit(self, text: str, idem_key: str) -> None:
        self.queue.put_nowait((text, idem_key))

    def close(self) -> None:
        self.queue.put_nowait(None)

    async def stop(self) -> None:
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        try:
            await self._recover()
        except Exception:
            log.exception("session %s: recovery failed", self.session_id)
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    await self._close()
                    return
                await self._handle(*item)
            except Exception:
                log.exception("session %s: %s failed", self.session_id, item)

    async def _state(self) -> SessionState:
        return fold(await events.read(self.pool, self.session_id))

    async def _append(self, type: str, payload: dict[str, Any], idem_key: str | None = None) -> int:
        return await events.append(self.pool, self.session_id, type, payload, idem_key)

    async def _recover(self) -> None:
        started = time.perf_counter()
        was_running = (await self._state()).run == "running"
        await recovery.reconcile(self.pool, self.session_id)
        state = await self._state()
        if was_running:
            ms = (time.perf_counter() - started) * 1000
            log.warning("session %s: reconciled an open run in %.0f ms", self.session_id, ms)
            if state.sandbox == "attached":  # keep what the run changed, as after any run
                assert state.sandbox_name is not None
                sb = await sandbox.attach(state.sandbox_name)
                if sb is not None:
                    await self._save_workspace(sb, state)
        if recovery.needs_run(state):  # a saved workspace does not change the answer
            await self._run()

    async def _handle(self, text: str, idem_key: str) -> None:
        state = await self._state()
        if state.session not in OPEN:
            log.warning("session %s is %s: message dropped", self.session_id, state.session)
            return
        seq = await self._append("user_message", {"text": text}, idem_key)
        if seq <= state.last_seq:
            return  # a retry of a message that is already in the log
        await self._run()

    async def _run(self) -> None:
        """Run the harness on the transcript, which ends with the last user message."""
        state = await self._state()
        assert state.harness is not None
        harness = HARNESSES[state.harness]
        run_id = uuid4().hex
        payload = {"run_id": run_id, "harness": harness.name, "message_seq": state.message_seq}
        await self._append("run_started", payload)
        terminal = False
        sb: Sandbox | None = None
        deadline = asyncio.timeout(config.RUN_TIMEOUT_S)
        try:
            async with deadline:
                sb = await self._sandbox(state, harness.placement)
                async for ev in harness.run(state.transcript, sb, run_id):
                    if ev.rseq is None:
                        self.fanout.publish_delta(self.session_id, {"type": ev.type, **ev.payload})
                        continue
                    await self._append(ev.type, ev.payload, f"{run_id}:{ev.rseq}")
                    terminal = terminal or ev.type in TERMINAL_RUN_EVENTS
            if not terminal:
                await self._append("run_failed", {"run_id": run_id, "reason": "no_terminal_event"})
        except Exception as exc:
            timed_out = isinstance(exc, TimeoutError) and deadline.expired()
            if not timed_out:
                log.exception("session %s: run %s failed", self.session_id, run_id)
            if not terminal:
                failure = (
                    {"reason": "timeout", "limit_s": config.RUN_TIMEOUT_S}
                    if timed_out
                    else {"reason": "error", "error": repr(exc)}
                )
                await self._append("run_failed", {"run_id": run_id, **failure})
            if sb is None and state.sandbox == "none" and isinstance(exc, sandbox.SandboxError):
                # The repo could not be set up even once (bad URL, clone failed): give up.
                await self._append("session_failed", {"reason": "provisioning", "error": repr(exc)})
        if sb is not None:  # also after a failed run: its edits are in the VM all the same
            await self._save_workspace(sb, state)

    async def _sandbox(self, state: SessionState, placement: Literal["host", "sandbox"]) -> Sandbox:
        """The session's VM: reattached if it still runs, otherwise (re)created."""
        if state.sandbox == "attached":
            assert state.sandbox_name is not None
            sb = await sandbox.attach(state.sandbox_name)
            if sb is not None:
                return sb
            lost = {"name": state.sandbox_name, "reason": "not_running"}
            await self._append("sandbox_lost", lost)
        elif state.sandbox == "provisioning":  # a run timed out while the VM was being set up
            reason = "provisioning_interrupted"
            await self._append("sandbox_lost", {"name": state.sandbox_name, "reason": reason})
        name = sandbox.sandbox_name(self.session_id)
        await self._append("sandbox_provisioning", {"name": name})
        assert state.repo_url is not None
        workspace = base64.b64decode(state.workspace) if state.workspace else None
        try:
            sb, commit = await sandbox.provision(
                name, state.repo_url, placement, state.commit, workspace
            )
        except Exception as exc:
            await self._append(
                "sandbox_lost", {"name": name, "reason": "provisioning_failed", "error": repr(exc)}
            )
            raise
        await self._append("sandbox_attached", {"name": name, "commit": commit})
        return sb

    async def _save_workspace(self, sb: Sandbox, state: SessionState) -> None:
        patch = base64.b64encode(await sandbox.save_workspace(sb)).decode()
        if patch != state.workspace:
            await self._append("workspace_saved", {"patch_b64": patch})

    async def _close(self) -> None:
        """Destroy the VM first, then close: a crash in between leaves an open session
        without a VM, never a closed session whose VM keeps running."""
        state = await self._state()
        if state.session not in OPEN:
            return
        if state.sandbox != "destroyed":
            await sandbox.destroy(self.session_id)
            name = sandbox.sandbox_name(self.session_id)
            await self._append("sandbox_destroyed", {"name": name})
        await self._append("session_closed", {})


async def fork_session(pool: asyncpg.Pool, source_id: UUID, at_seq: int) -> UUID:
    """A new session with the source's log up to `at_seq` and a VM of its own.

    The VM is a copy-on-write `fork()` of the source's VM when the fork is at the
    source's head and that VM is running; otherwise it is rebuilt from the repo
    plus the workspace patch saved at or before `at_seq`. Host-placement VMs are
    always rebuilt: microsandbox does not fork a VM with the `Network.none()` policy.
    Raises LookupError for an unknown session and ValueError for a seq that is
    out of range or in the middle of a run.
    """
    log = await events.read(pool, source_id)
    if not log or log[0].type != "session_created":
        raise LookupError(f"unknown session {source_id}")
    if not 1 <= at_seq <= len(log):
        raise ValueError(f"at_seq must be between 1 and {len(log)}")
    prefix = fold(log[:at_seq])
    if prefix.session not in OPEN:
        raise ValueError(f"cannot fork at seq {at_seq}: the session is {prefix.session} there")
    head = fold(log)
    fork_id: UUID = await pool.fetchval("SELECT uuidv7()")
    await events.fork(pool, source_id, fork_id, at_seq)
    name = sandbox.sandbox_name(fork_id)
    await events.append(pool, fork_id, "sandbox_provisioning", {"name": name})
    assert prefix.harness is not None and prefix.repo_url is not None
    placement = HARNESSES[prefix.harness].placement
    source_vm = None
    if at_seq == head.last_seq and head.sandbox == "attached" and placement == "sandbox":
        assert head.sandbox_name is not None
        source_vm = await sandbox.attach(head.sandbox_name)
    if source_vm is not None:
        sb, commit, via = await sandbox.fork(source_vm, name), prefix.commit, "fork"
    else:
        workspace = base64.b64decode(prefix.workspace) if prefix.workspace else None
        sb, commit = await sandbox.provision(
            name, prefix.repo_url, placement, prefix.commit, workspace
        )
        via = "rebuild"
    await events.append(
        pool, fork_id, "sandbox_attached", {"name": name, "commit": commit, "via": via}
    )
    return fork_id
