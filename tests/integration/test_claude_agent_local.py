"""B-local for real: the Claude Agent SDK in the VM, the host's Ollama as the brain.

The VM has no network; its only way out is the vsock port behind which the
Ollama bridge passes `POST /v1/messages` and nothing else. No key of any kind is
involved, so nothing here can leak one.
"""

import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from microsandbox import Sandbox

from lab import config
from lab.harnesses import HARNESSES
from lab.harnesses.claude_agent import SDK_HOST
from lab.harnesses.claude_agent_local import BRIDGE, VSOCK_PORT
from lab.sandbox import destroy, provision, sandbox_name
from tests.integration.conftest import ollama_has_model, say_and_wait


def _ollama_up() -> bool:
    try:
        httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=5).raise_for_status()
    except httpx.HTTPError:
        return False
    return True


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not SDK_HOST.is_dir(), reason="run make claude-sdk"),
    pytest.mark.skipif(not _ollama_up(), reason="Ollama on the host needed"),
]

WORD = "HERON-17"

# Inside the VM: one HTTP request to the host over vsock, and the two other ways
# out (DeepSeek by name, Ollama's port on the host over TCP), which must fail.
_PROBE = """
import json, socket, sys, urllib.request

def vsock(method, path, body=b""):
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect((2, int(sys.argv[1])))
    head = f"{method} {path} HTTP/1.1\\r\\nHost: x\\r\\nContent-Type: application/json\\r\\n"
    s.sendall(f"{head}Content-Length: {len(body)}\\r\\n\\r\\n".encode() + body)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    return int(data.split(b" ")[1])

def fails(fn):
    try:
        fn()
    except Exception:
        return True
    return False

unknown_model = json.dumps({"model": "no-such-model", "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}]}).encode()
print(json.dumps({
    "messages": vsock("POST", "/v1/messages?beta=true", unknown_model),
    "tags": vsock("GET", "/api/tags"),
    "pull": vsock("POST", "/api/pull", b'{"model": "x"}'),
    "delete": vsock("DELETE", "/api/delete", b'{"model": "x"}'),
    "deepseek": fails(lambda: urllib.request.urlopen("https://api.deepseek.com", timeout=10)),
    "host_tcp": fails(lambda: socket.create_connection(("host.microsandbox.internal", 11434), 10)),
}))
"""


@pytest.fixture
async def local_vm(calc_repo: str) -> AsyncIterator[Sandbox]:
    await BRIDGE.start()
    session_id = uuid.uuid4()
    try:
        spec = HARNESSES["claude_agent_local"].vm_spec()
        sb, _ = await provision(sandbox_name(session_id), calc_repo, spec)
        yield sb
    finally:
        await destroy(session_id)
        await BRIDGE.stop()


async def _probe(sb: Sandbox) -> dict[str, Any]:
    out = await sb.exec("python3", ["-c", _PROBE, str(VSOCK_PORT)], timeout=90)
    assert out.exit_code == 0, out.stderr_text
    result: dict[str, Any] = json.loads(out.stdout_text)
    return result


async def test_vm_reaches_only_ollama_messages(
    local_vm: Sandbox, assert_no_network: Callable[[Sandbox], Awaitable[None]]
) -> None:
    await assert_no_network(local_vm)  # deny-all policy: no DNS, no IP traffic
    probe = await _probe(local_vm)
    # The Messages API reaches Ollama (its own 404 for an unknown model, not the
    # bridge's 403); Ollama's own API is refused by the bridge.
    assert probe["messages"] == 404, probe
    assert (probe["tags"], probe["pull"], probe["delete"]) == (403, 403, 403), probe
    assert probe["deepseek"] and probe["host_tcp"], probe

    # No key of any kind, and no secret placeholders: nothing for DeepSeek.
    env = (await local_vm.exec("env", [])).stdout_text
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    leaked = bool(key) and key in env
    assert not leaked, "the DeepSeek key is visible inside the VM"
    assert "MSB_" not in env and "deepseek" not in env.lower()

    # A restarted bridge (as after an API restart) serves the same VM again.
    await BRIDGE.stop()
    await BRIDGE.start()
    assert (await _probe(local_vm))["messages"] == 404


def _since(log: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    start = max(
        i for i, e in enumerate(log) if e["type"] == "user_message" and e["payload"]["text"] == text
    )
    return log[start:]


def _one(log: list[dict[str, Any]], type: str) -> dict[str, Any]:
    found = [e for e in log if e["type"] == type]
    assert len(found) == 1, [e["type"] for e in log]
    return found[0]


@pytest.mark.slow
@pytest.mark.skipif(not ollama_has_model(), reason=f"Ollama with {config.OLLAMA_MODEL} needed")
async def test_tool_task_then_switch_to_deepagents(api: str, calc_repo: str) -> None:
    body = {"repo_url": calc_repo, "harness": "claude_agent_local"}
    session_id = httpx.post(f"{api}/sessions", json=body).json()["session_id"]
    try:
        first = (
            f"Remember this code word for later: {WORD}. Then run the tests with"
            " `python -m unittest` and tell me which test fails. Do not change any file."
        )
        turn = _since(await say_and_wait(api, session_id, first), first)
        assert _one(turn, "sandbox_attached")["payload"]["harness"] == "claude_agent_local"
        assert any(e["type"] == "tool_call" for e in turn)
        assert "average" in _one(turn, "agent_message")["payload"]["text"]
        finished = _one(turn, "run_finished")["payload"]
        assert finished["model"] == config.OLLAMA_MODEL
        assert finished["cost_usd"] == 0 and finished["usage"]["output_tokens"] > 0

        r = httpx.post(f"{api}/sessions/{session_id}/harness", json={"name": "deepagents_ollama"})
        assert r.status_code == 202
        second = "What was the code word I gave you earlier? Reply with just the code word."
        turn = _since(await say_and_wait(api, session_id, second), second)
        assert _one(turn, "run_started")["payload"]["harness"] == "deepagents_ollama"
        assert _one(turn, "sandbox_destroyed")["payload"]["reason"] == "harness_switched"
        assert WORD in _one(turn, "agent_message")["payload"]["text"]
        _one(turn, "run_finished")
    finally:
        await destroy(uuid.UUID(session_id))
