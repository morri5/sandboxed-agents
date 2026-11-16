"""Architecture A end to end: the deepagents loop in the API process, Ollama as the
brain, tools in a VM without network, on the calc-repo fixture."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from microsandbox import Sandbox

from lab import config
from lab.sandbox import REPO_DIR, destroy, sandbox_name
from tests.integration.conftest import ollama_has_model, until_run_end

pytestmark = [pytest.mark.integration, pytest.mark.slow]

RUN_TIMEOUT = 900  # seconds; a local model on a cold start is slow


@pytest.mark.skipif(not ollama_has_model(), reason=f"Ollama with {config.OLLAMA_MODEL} needed")
async def test_agent_fixes_the_failing_test(
    api: str, calc_repo: str, assert_no_network: Callable[[Sandbox], Awaitable[None]]
) -> None:
    body = {"repo_url": calc_repo, "harness": "deepagents_ollama"}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        message = {
            "text": "One test in this repo fails. Find the bug and fix the code so all tests pass.",
            "idem_key": str(uuid.uuid4()),
        }
        assert httpx.post(f"{api}/sessions/{session_id}/messages", json=message).status_code == 202
        async with asyncio.timeout(RUN_TIMEOUT):
            while True:
                log = until_run_end(httpx.get(f"{api}/sessions/{session_id}/events").json())
                if log is not None:
                    break
                await asyncio.sleep(2)

        types = [e["type"] for e in log]
        assert types[-1] == "run_finished", log[-1]
        assert types.count("agent_message") == 1
        assert "tool_result" in types
        run_id = next(e["payload"]["run_id"] for e in log if e["type"] == "run_started")
        assert all(e["idem_key"].startswith(f"{run_id}:") for e in log[types.index("tool_call") :])
        assert log[-1]["payload"]["usage"]["output_tokens"] > 0

        vm = await (await Sandbox.get(sandbox_name(uuid.UUID(session_id)))).connect()
        await assert_no_network(vm)
        tests = await vm.exec("python", ["-m", "unittest"], cwd=REPO_DIR, timeout=60)
        assert tests.exit_code == 0, tests.stderr_text
        diff = await vm.exec("git", ["diff", "--stat"], cwd=REPO_DIR)
        assert "calc/__init__.py" in diff.stdout_text
        assert "tests/" not in diff.stdout_text  # fixed the code, not the test
    finally:
        await destroy(uuid.UUID(session_id))
