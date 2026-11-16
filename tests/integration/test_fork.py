"""Fork a chat at seq N: the fork gets the log up to N and a VM of its own.

At the source's head the VM is a copy-on-write `fork()` of the source's VM; at
an earlier seq it is rebuilt from the repo plus the workspace patch saved at
that point. Uses the scripted harness (in-VM, deterministic) through the API.
"""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from microsandbox import Sandbox

from lab.events import TERMINAL_RUN_EVENTS
from lab.sandbox import REPO_DIR, attach, destroy, sandbox_name

pytestmark = pytest.mark.integration

RUN_TIMEOUT = 600


def _events(api: str, session_id: str) -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = httpx.get(f"{api}/sessions/{session_id}/events").json()
    return log


def _run_ended(log: list[dict[str, Any]], text: str) -> bool:
    """The run for the message `text` has ended."""
    sent = [
        i for i, e in enumerate(log) if e["type"] == "user_message" and e["payload"]["text"] == text
    ]
    return bool(sent) and any(e["type"] in TERMINAL_RUN_EVENTS for e in log[sent[-1] :])


async def _say_and_wait(api: str, session_id: str, text: str) -> list[dict[str, Any]]:
    """Send a message; return the log once its run ended and the log is quiet (the
    workspace snapshot after a run may add one more event)."""
    body = {"text": text, "idem_key": str(uuid.uuid4())}
    assert httpx.post(f"{api}/sessions/{session_id}/messages", json=body).status_code == 202
    previous = None
    async with asyncio.timeout(RUN_TIMEOUT):
        while True:
            await asyncio.sleep(1)
            log = _events(api, session_id)
            if _run_ended(log, text) and log == previous:
                return log
            previous = log


async def _vm(session_id: str) -> Sandbox:
    vm = await attach(sandbox_name(uuid.UUID(session_id)))
    assert vm is not None, f"no running VM for {session_id}"
    return vm


async def _exists(vm: Sandbox, path: str) -> bool:
    return (await vm.exec("test", ["-e", f"{REPO_DIR}/{path}"])).exit_code == 0


def _fork(api: str, session_id: str, at_seq: int) -> str:
    r = httpx.post(f"{api}/sessions/{session_id}/fork", json={"at_seq": at_seq})
    assert r.status_code == 201, r.text
    fork_id: str = r.json()["session_id"]
    return fork_id


async def test_forks_diverge(api: str, calc_repo: str) -> None:
    body = {"repo_url": calc_repo, "harness": "scripted"}
    source = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    sessions = [source]
    try:
        await _say_and_wait(api, source, "first")
        # An edit in the source VM, as an agent would make; the next run's snapshot keeps it.
        await (await _vm(source)).shell(f"echo source > {REPO_DIR}/notes.txt")
        before_second = len(_events(api, source))
        log = await _say_and_wait(api, source, "second")
        assert log[-1]["type"] == "workspace_saved"

        # In the middle of a run is refused.
        mid_run = next(e["seq"] for e in log if e["type"] == "tool_call")
        r = httpx.post(f"{api}/sessions/{source}/fork", json={"at_seq": mid_run})
        assert r.status_code == 409

        at_head = _fork(api, source, len(log))
        early = _fork(api, source, before_second)
        sessions += [at_head, early]

        head_log, early_log = _events(api, at_head), _events(api, early)
        strip = ("session_id",)
        assert [{k: v for k, v in e.items() if k not in strip} for e in head_log[: len(log)]] == [
            {k: v for k, v in e.items() if k not in strip} for e in log
        ]
        assert head_log[len(log)]["type"] == "forked_from"
        assert head_log[len(log)]["payload"] == {"session_id": source, "at_seq": len(log)}
        assert head_log[-1]["payload"]["via"] == "fork"  # copy-on-write of the live VM
        assert early_log[before_second]["type"] == "forked_from"
        assert early_log[-1]["payload"]["via"] == "rebuild"  # repo plus saved workspace

        names = {e["payload"]["name"] for e in (head_log[-1], early_log[-1])}
        assert names == {sandbox_name(uuid.UUID(at_head)), sandbox_name(uuid.UUID(early))}
        source_vm, head_vm, early_vm = [await _vm(s) for s in sessions]
        assert await _exists(head_vm, "notes.txt")  # the head fork sees the source's edit
        assert not await _exists(early_vm, "notes.txt")  # the early one is from before it
        assert await _exists(early_vm, "calc/__init__.py")

        # The fork goes its own way: its VM and log change, the source's do not.
        await head_vm.shell(f"echo fork > {REPO_DIR}/only-in-fork.txt")
        assert not await _exists(source_vm, "only-in-fork.txt")
        source_len = len(_events(api, source))
        head_after = await _say_and_wait(api, at_head, "third, only in the fork")
        assert len(_events(api, source)) == source_len
        assert [e["seq"] for e in head_after] == list(range(1, len(head_after) + 1))
        assert [e["payload"]["text"] for e in head_after if e["type"] == "user_message"] == [
            "first",
            "second",
            "third, only in the fork",
        ]
    finally:
        for session_id in sessions:
            await destroy(uuid.UUID(session_id))
