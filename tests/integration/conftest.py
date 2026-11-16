"""Shared fixtures for tests that run real microVMs."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from microsandbox import Sandbox

from lab import config
from lab.events import TERMINAL_RUN_EVENTS
from lab.sandbox import destroy, provision, sandbox_name

CALC_REPO = str(Path(__file__).resolve().parents[2] / "fixtures" / "calc-repo")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def start_api(dsn: str, port: int, **env: str) -> subprocess.Popen[bytes]:
    """Start the API as its own process and wait until it answers."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", str(port)]
        + ["--log-level", "warning"],
        env={**os.environ, "LAB_DATABASE_URL": dsn, **env},
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"http://127.0.0.1:{port}/docs").raise_for_status()
            return proc
        except httpx.HTTPError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise
            time.sleep(0.1)


def stop_api(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE events")
    finally:
        await conn.close()


@pytest.fixture
def clean_db(test_dsn: str) -> str:
    """An empty event log, so the API's startup recovery sees no other test's sessions."""
    asyncio.run(_truncate(test_dsn))
    return test_dsn


@pytest.fixture
def api(clean_db: str) -> Iterator[str]:
    port = free_port()
    proc = start_api(clean_db, port)
    yield f"http://127.0.0.1:{port}"
    stop_api(proc)


@pytest.fixture
async def host_vm() -> AsyncIterator[Sandbox]:
    """A VM as the host-placement harness gets it: calc-repo copied in, no network."""
    session_id = uuid.uuid4()
    sb, _ = await provision(sandbox_name(session_id), CALC_REPO, "host")
    yield sb
    await destroy(session_id)


_PROBE = """
import json, socket, urllib.error, urllib.request
result = {}
try:
    socket.gethostbyname("example.com")
    result["dns"] = "resolved"
except OSError as exc:
    result["dns"] = repr(exc)
try:
    result["http"] = urllib.request.urlopen("http://1.1.1.1", timeout=5).status
except urllib.error.HTTPError as exc:
    result["http"] = exc.read().decode()
except OSError as exc:
    result["http"] = repr(exc)
print(json.dumps(result))
"""


async def _assert_no_network(sb: Sandbox) -> None:
    """The VM's policy denies all traffic, and traffic really is blocked."""
    policy = (await Sandbox.get(await sb.name)).config()["network"]["policy"]
    assert policy == {"default_egress": "deny", "default_ingress": "deny", "rules": []}
    out = await sb.exec("python3", ["-c", _PROBE], timeout=30)
    probe = json.loads(out.stdout_text)
    assert probe["dns"] != "resolved", probe
    # A literal IP skips DNS; microsandbox's policy proxy answers instead of the host.
    assert "network_policy_denied" in str(probe["http"]), probe


@pytest.fixture
def assert_no_network() -> Callable[[Sandbox], Awaitable[None]]:
    return _assert_no_network


def ollama_has_model() -> bool:
    try:
        tags = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=5).json()
    except httpx.HTTPError:
        return False
    return any(m["name"] == config.OLLAMA_MODEL for m in tags["models"])


def until_run_end(log: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The log cut after its first run's terminal event (a workspace snapshot may
    follow it), or None while that run is still going."""
    for i, e in enumerate(log):
        if e["type"] in TERMINAL_RUN_EVENTS:
            return log[: i + 1]
    return None


@pytest.fixture
def calc_repo() -> str:
    return CALC_REPO
