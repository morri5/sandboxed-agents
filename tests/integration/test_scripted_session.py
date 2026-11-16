"""One chat end to end: real API process, real microVM, scripted harness."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import websockets

from lab.sandbox import destroy

pytestmark = pytest.mark.integration

REPO = "https://github.com/pallets/markupsafe"  # small, pure pytest suite, no extra test deps
RUN_TIMEOUT = 600  # seconds; includes the first pull of the python:3.13 image


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def api(test_dsn: str) -> Iterator[str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", str(port)],
        env={**os.environ, "LAB_DATABASE_URL": test_dsn},
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"{url}/docs").raise_for_status()
            break
        except httpx.HTTPError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise
            time.sleep(0.2)
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise


async def test_scripted_chat_end_to_end(api: str) -> None:
    r = httpx.post(f"{api}/sessions", json={"repo_url": REPO, "harness": "scripted"})
    assert r.status_code == 201
    session_id = r.json()["session_id"]
    assert uuid.UUID(session_id).version == 7
    try:
        stream_url = f"{api.replace('http', 'ws', 1)}/sessions/{session_id}/stream"
        streamed: list[dict[str, Any]] = []
        async with websockets.connect(stream_url) as ws:
            message = {"text": "Do the tests pass?", "idem_key": str(uuid.uuid4())}
            for _ in range(2):  # a retried POST must not create a second message or run
                r = httpx.post(f"{api}/sessions/{session_id}/messages", json=message)
                assert r.status_code == 202
            async with asyncio.timeout(RUN_TIMEOUT):
                async for raw in ws:
                    item = json.loads(raw)
                    streamed.append(item)
                    if item["kind"] == "event" and item["type"] in ("run_finished", "run_failed"):
                        break

        log = httpx.get(f"{api}/sessions/{session_id}/events").json()
        types = [e["type"] for e in log]
        assert types[-1] == "run_finished", log[-1]
        assert types.count("user_message") == 1
        assert types.count("run_started") == 1
        assert [e["seq"] for e in log] == list(range(1, len(log) + 1))

        pytest_call = next(
            e
            for e in log
            if e["type"] == "tool_call" and "pytest -q" in e["payload"]["args"].get("command", "")
        )
        result = next(
            e
            for e in log
            if e["type"] == "tool_result" and e["payload"]["id"] == pytest_call["payload"]["id"]
        )
        assert result["payload"]["exit_code"] == 0
        assert " passed" in result["payload"]["stdout"]
        run_id = next(e["payload"]["run_id"] for e in log if e["type"] == "run_started")
        assert result["idem_key"].startswith(f"{run_id}:")  # "<run_id>:<rseq>" from the runner

        # The stream delivered the same durable events in order, plus live deltas.
        assert [i["seq"] for i in streamed if i["kind"] == "event"] == [e["seq"] for e in log]
        deltas = "".join(i["payload"]["text"] for i in streamed if i["kind"] == "delta")
        assert deltas.strip() == log[-2]["payload"]["text"]
        assert "Do the tests pass?" in log[-2]["payload"]["text"]
    finally:
        await destroy(uuid.UUID(session_id))
