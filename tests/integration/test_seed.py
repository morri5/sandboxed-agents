"""Seed files through the API in a real VM (scripted harness): present in the working
tree, never in a workspace patch, and back after the VM is lost and rebuilt."""

import base64
import uuid

import httpx
import pytest

from lab.sandbox import REPO_DIR, attach, destroy, sandbox_name
from tests.integration.conftest import say_and_wait

pytestmark = pytest.mark.integration

SEED = {
    "thoughts/shared/factory/research/2026-11-20-r.md": "# Research\n",
    "thoughts/shared/factory/plans/2026-11-20-p.md": "# Plan\n",
}


async def _read_seed(session_id: str) -> dict[str, str]:
    vm = await attach(sandbox_name(uuid.UUID(session_id)))
    assert vm is not None
    return {p: (await vm.fs.read(f"{REPO_DIR}/{p}")).decode() for p in SEED}


async def test_seed_files_are_context_not_changes(api: str, calc_repo: str) -> None:
    body = {"repo_url": calc_repo, "harness": "scripted", "seed_files": SEED}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        log = await say_and_wait(api, session_id, "first")
        assert log[0]["payload"]["seed_files"] == SEED
        assert await _read_seed(session_id) == SEED
        vm = await attach(sandbox_name(uuid.UUID(session_id)))
        assert vm is not None
        await vm.shell(f"echo edit > {REPO_DIR}/notes.txt")  # an agent's change
        log = await say_and_wait(api, session_id, "second")
        patches = [
            base64.b64decode(e["payload"]["patch_b64"])
            for e in log
            if e["type"] == "workspace_saved"
        ]
        assert b"notes.txt" in patches[-1]  # the snapshot sees the edit, not the seed
        assert all(b"thoughts/" not in patch for patch in patches)

        await destroy(uuid.UUID(session_id))  # the VM is lost
        log = await say_and_wait(api, session_id, "third")
        types = [e["type"] for e in log]
        assert "sandbox_lost" in types and types.count("sandbox_attached") == 2
        assert await _read_seed(session_id) == SEED  # rebuilt with the seed
        vm = await attach(sandbox_name(uuid.UUID(session_id)))
        assert vm is not None
        assert (await vm.exec("test", ["-e", f"{REPO_DIR}/notes.txt"])).exit_code == 0
        saved = [e["payload"]["patch_b64"] for e in log if e["type"] == "workspace_saved"]
        assert all(b"thoughts/" not in base64.b64decode(patch) for patch in saved)
    finally:
        await destroy(uuid.UUID(session_id))
