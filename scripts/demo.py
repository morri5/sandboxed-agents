"""`make demo`: one chat end to end, printed as it happens.

Usage: python scripts/demo.py [HARNESS]   (default: scripted)

Starts its own API on a free port (stop `make api` first: two APIs on one database
would both reconcile the same sessions), creates a session on `fixtures/calc-repo`,
sends one message and streams every event until the session is closed: the close is
queued right behind the run, so the stream ends after the run, its workspace snapshot
and the VM's destruction. Then the API is stopped.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets

from lab.cli import render
from lab.harnesses import HARNESSES

CALC_REPO = str(Path(__file__).resolve().parents[1] / "fixtures" / "calc-repo")
MESSAGE = "One test fails. Find out why and fix the code, not the test."


def start_api() -> tuple[subprocess.Popen[bytes], str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", str(port)]
        + ["--log-level", "warning"],
        env=os.environ,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"{url}/docs").raise_for_status()
            return proc, url
        except httpx.HTTPError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise
            time.sleep(0.1)


async def chat(url: str, harness: str) -> None:
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        r = await client.post("/sessions", json={"repo_url": CALC_REPO, "harness": harness})
        r.raise_for_status()
        session_id = r.json()["session_id"]
        print(f"session {session_id} on fixtures/calc-repo, harness {harness}")
        ws_url = f"{url.replace('http', 'ws', 1)}/sessions/{session_id}/stream"
        async with websockets.connect(ws_url) as ws:
            body = {"text": MESSAGE, "idem_key": str(uuid.uuid4())}
            (await client.post(f"/sessions/{session_id}/messages", json=body)).raise_for_status()
            (await client.delete(f"/sessions/{session_id}")).raise_for_status()
            streamed = False  # the previous item was a delta
            async for message in ws:
                item = json.loads(message)
                sys.stdout.write(render(item, streamed))
                sys.stdout.flush()
                streamed = item["kind"] == "delta"
                if item["kind"] == "event" and item["type"] == "session_closed":
                    return


def main() -> None:
    harness = sys.argv[1] if len(sys.argv) > 1 else "scripted"
    if harness not in HARNESSES:
        raise SystemExit(f"unknown harness {harness!r}, known: {sorted(HARNESSES)}")
    HARNESSES[harness].vm_spec()  # fail now on a missing key or SDK
    proc, url = start_api()
    try:
        asyncio.run(chat(url, harness))
    finally:
        proc.terminate()
        proc.wait(timeout=30)


if __name__ == "__main__":
    main()
