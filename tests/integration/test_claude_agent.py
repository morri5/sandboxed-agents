"""Architecture B for real: the Claude Agent SDK in the VM, DeepSeek as the brain.

Needs DEEPSEEK_API_KEY (skipped without it) and spends a few DeepSeek tokens per
test; the switch test also needs Ollama. Nothing here may print the key: checks
on it are booleans computed first, so a failing assert cannot show its value.
"""

import os
import uuid
from typing import Any

import httpx
import pytest

from lab import config
from lab.harnesses.claude_agent import SDK_HOST
from lab.sandbox import attach, destroy, sandbox_name
from tests.integration.conftest import (
    events,
    free_port,
    ollama_has_model,
    say_and_wait,
    start_api,
    stop_api,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY needed"),
    pytest.mark.skipif(not SDK_HOST.is_dir(), reason="run make claude-sdk"),
]

WORD = "PELICAN-42"


def _since(log: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The events from the user message `text` on."""
    start = max(
        i for i, e in enumerate(log) if e["type"] == "user_message" and e["payload"]["text"] == text
    )
    return log[start:]


def _one(log: list[dict[str, Any]], type: str) -> dict[str, Any]:
    found = [e for e in log if e["type"] == type]
    assert len(found) == 1, [e["type"] for e in log]
    return found[0]


async def _assert_placeholder_only(session_id: str) -> None:
    """Inside the VM the key variables hold microsandbox's placeholder, not the key."""
    vm = await attach(sandbox_name(uuid.UUID(session_id)))
    assert vm is not None
    out = await vm.exec("printenv", ["ANTHROPIC_AUTH_TOKEN"])
    value = out.stdout_text.strip()
    leaked = os.environ["DEEPSEEK_API_KEY"] in out.stdout_text
    placeholder = value == "$MSB_ANTHROPIC_AUTH_TOKEN"
    assert not leaked, "the real key is visible inside the VM"
    assert placeholder, "ANTHROPIC_AUTH_TOKEN is not the placeholder"


@pytest.mark.skipif(not ollama_has_model(), reason=f"Ollama with {config.OLLAMA_MODEL} needed")
async def test_switch_from_deepagents_to_claude_agent(api: str, calc_repo: str) -> None:
    body = {"repo_url": calc_repo, "harness": "deepagents_ollama"}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        first = f"Remember this code word for later: {WORD}. Do not use any tools, just reply OK."
        log = await say_and_wait(api, session_id, first)
        assert _one(_since(log, first), "run_finished")

        r = httpx.post(f"{api}/sessions/{session_id}/harness", json={"name": "claude_agent"})
        assert r.status_code == 202
        second = "What was the code word I gave you earlier? Reply with just the code word."
        log = await say_and_wait(api, session_id, second)
        turn = _since(log, second)
        assert _one(log, "harness_switched")["payload"] == {
            "from": "deepagents_ollama",
            "to": "claude_agent",
        }
        assert _one(turn, "run_started")["payload"]["harness"] == "claude_agent"
        # The tools-only VM cannot host this harness: it is replaced, with the workspace.
        assert _one(turn, "sandbox_destroyed")["payload"]["reason"] == "harness_switched"
        assert _one(turn, "sandbox_attached")["payload"]["harness"] == "claude_agent"
        assert WORD in _one(turn, "agent_message")["payload"]["text"]
        finished = _one(turn, "run_finished")["payload"]
        assert finished["resumed"] is False  # a new harness starts from the log's transcript
        assert finished["cost_usd"] > 0 and finished["usage"]["output_tokens"] > 0
        await _assert_placeholder_only(session_id)

        third = "Repeat that code word once more, in lowercase. Reply with just the word."
        turn = _since(await say_and_wait(api, session_id, third), third)
        assert "sandbox_attached" not in [e["type"] for e in turn]  # same VM
        assert _one(turn, "run_finished")["payload"]["resumed"] is True  # its own session
        assert WORD.lower() in _one(turn, "agent_message")["payload"]["text"]
        print(
            "\nusage per claude_agent run:",
            [e["payload"]["usage"] for e in events(api, session_id) if e["type"] == "run_finished"][
                1:
            ],
        )
    finally:
        await destroy(uuid.UUID(session_id))


async def test_budget_interrupts_a_real_run(clean_db: str, calc_repo: str) -> None:
    """A budget below the cost of one model turn: the first turn's usage trips it,
    and the CLI is interrupted before the run gets anywhere."""
    port = free_port()
    proc = start_api(clean_db, port, LAB_MAX_USD_PER_RUN="0.00005")
    api = f"http://127.0.0.1:{port}"
    body = {"repo_url": calc_repo, "harness": "claude_agent"}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        text = "List the files with ls, then read README.md, then summarize the repo."
        turn = _since(await say_and_wait(api, session_id, text), text)
        failed = _one(turn, "run_failed")["payload"]
        assert failed["reason"] == "budget", failed
        assert failed["limit_usd"] == 0.00005
        assert failed["cost_usd"] > failed["limit_usd"]
        assert "agent_message" not in [e["type"] for e in turn]
        print("\nusage of the budget run:", failed["usage"])
    finally:
        await destroy(uuid.UUID(session_id))
        stop_api(proc)
