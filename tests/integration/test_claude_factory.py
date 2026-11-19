"""`claude_factory` for real: a command that delegates to a subagent, in a VM, on DeepSeek.

The command (`tests/fixtures/commands/count_files.md`) has the agent call the
`codebase-locator` subagent (`tests/fixtures/agents/`) and write a file under
`thoughts/`. Needs DEEPSEEK_API_KEY (skipped without it) and spends a few cents.
"""

import base64
import os
import uuid
from pathlib import Path

import httpx
import pytest

from lab.harnesses.claude_agent import SDK_HOST
from lab.sandbox import destroy
from tests.integration.conftest import free_port, say_and_wait, start_api, stop_api

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY needed"),
    pytest.mark.skipif(not SDK_HOST.is_dir(), reason="run make claude-sdk"),
]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPORT = "thoughts/shared/research/count.md"


async def test_command_with_a_subagent(clean_db: str, calc_repo: str) -> None:
    port = free_port()
    proc = start_api(clean_db, port, LAB_FACTORY_AGENTS_DIR=str(FIXTURES / "agents"))
    api = f"http://127.0.0.1:{port}"
    body = {"repo_url": calc_repo, "harness": "claude_factory"}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        command = (FIXTURES / "commands" / "count_files.md").read_text()
        limits = {"max_tool_calls": 60, "run_timeout_s": 600, "max_usd": 0.5}
        log = await say_and_wait(api, session_id, command, fresh_context=True, limits=limits)

        types = [e["type"] for e in log]
        finished = next(e["payload"] for e in log if e["type"] == "run_finished")
        assert "run_failed" not in types
        assert finished["cost_usd"] > 0
        assert len(finished["agents_sha"]) == 64
        calls = [e["payload"] for e in log if e["type"] == "tool_call"]
        assert any(c["name"] == "Agent" for c in calls), calls
        assert any(c["parent"] is not None for c in calls), calls
        patch = next(e for e in reversed(log) if e["type"] == "workspace_saved")
        assert REPORT.encode() in base64.b64decode(patch["payload"]["patch_b64"])
        subagent_calls = sum(c["parent"] is not None for c in calls)
        print(f"\nfactory run: {len(calls)} tool calls ({subagent_calls} by subagents),", finished)
    finally:
        await destroy(uuid.UUID(session_id))
        stop_api(proc)
