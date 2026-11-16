"""FastAPI session service: REST to create sessions and send messages, WebSocket to stream.

Run with `uvicorn lab.api:app` (`make api`).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lab import events
from lab.db import DATABASE_URL, create_pool
from lab.fanout import FanOut
from lab.harnesses import HARNESSES
from lab.session import SessionRunner, fork_session
from lab.state import OPEN, fold


class NewSession(BaseModel):
    repo_url: str
    harness: str = "scripted"


class NewMessage(BaseModel):
    text: str
    idem_key: str


class Fork(BaseModel):
    at_seq: int


class SwitchHarness(BaseModel):
    name: str


def _known_harness(name: str) -> None:
    if name not in HARNESSES:
        raise HTTPException(422, f"unknown harness {name!r}, known: {sorted(HARNESSES)}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(DATABASE_URL)
    fanout = FanOut(pool, DATABASE_URL)
    await fanout.start()
    app.state.pool = pool
    app.state.fanout = fanout
    # Every open session gets its runner now; each one first reconciles its session
    # with what survived (VM, runner event file), in the background.
    app.state.runners = {
        sid: SessionRunner(pool, fanout, sid) for sid in await events.open_sessions(pool)
    }
    try:
        yield
    finally:
        for runner in app.state.runners.values():
            await runner.stop()
        await fanout.close()
        await pool.close()


app = FastAPI(title="sandboxed-agents-lab", lifespan=lifespan)


async def _runner(request: Request, session_id: UUID) -> SessionRunner:
    runners: dict[UUID, SessionRunner] = request.app.state.runners
    if session_id not in runners:
        first = await events.read(request.app.state.pool, session_id, 0)
        if not first or first[0].type != "session_created":
            raise HTTPException(404, "unknown session")
        if session_id not in runners:  # re-check: another request may have won the await
            runners[session_id] = SessionRunner(
                request.app.state.pool, request.app.state.fanout, session_id
            )
    return runners[session_id]


@app.post("/sessions", status_code=201)
async def create_session(body: NewSession, request: Request) -> dict[str, Any]:
    _known_harness(body.harness)
    pool = request.app.state.pool
    session_id: UUID = await pool.fetchval("SELECT uuidv7()")
    payload = {"repo_url": body.repo_url, "harness": body.harness}
    await events.append(pool, session_id, "session_created", payload)
    await _runner(request, session_id)  # from here on, the runner is the session's only writer
    return {"session_id": str(session_id)}


async def _open_runner(request: Request, session_id: UUID) -> SessionRunner:
    runner = await _runner(request, session_id)
    state = fold(await events.read(request.app.state.pool, session_id))
    if state.session not in (*OPEN, "running"):
        raise HTTPException(409, f"session is {state.session}")
    return runner


@app.post("/sessions/{session_id}/messages", status_code=202)
async def send_message(session_id: UUID, body: NewMessage, request: Request) -> dict[str, Any]:
    runner = await _open_runner(request, session_id)
    runner.submit(body.text, body.idem_key)
    return {"accepted": True, "idem_key": body.idem_key}


@app.post("/sessions/{session_id}/fork", status_code=201)
async def fork(session_id: UUID, body: Fork, request: Request) -> dict[str, Any]:
    try:
        fork_id = await fork_session(request.app.state.pool, session_id, body.at_seq)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _runner(request, fork_id)  # from here on, the fork's runner is its only writer
    return {"session_id": str(fork_id)}


@app.post("/sessions/{session_id}/harness", status_code=202)
async def switch_harness(session_id: UUID, body: SwitchHarness, request: Request) -> dict[str, Any]:
    """Queued like a message: the next run uses this harness, on the transcript from the log."""
    _known_harness(body.name)
    runner = await _open_runner(request, session_id)
    runner.switch_harness(body.name)
    return {"accepted": True}


@app.delete("/sessions/{session_id}", status_code=202)
async def close_session(session_id: UUID, request: Request) -> dict[str, Any]:
    """Queued behind any pending message: the VM is destroyed and the session closed."""
    runner = await _open_runner(request, session_id)
    runner.close()
    return {"accepted": True}


@app.get("/sessions/{session_id}/events")
async def get_events(session_id: UUID, request: Request, after: int = 0) -> list[dict[str, Any]]:
    return [e.to_json() for e in await events.read(request.app.state.pool, session_id, after)]


@app.websocket("/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, session_id: UUID, after: int = 0) -> None:
    await websocket.accept()
    fanout: FanOut = websocket.app.state.fanout

    async def pump() -> None:
        async for item in fanout.subscribe(session_id, after):
            await websocket.send_json(item)

    pump_task = asyncio.create_task(pump())
    try:
        # The subscription blocks on its queue and would never see the client leave;
        # reading is what surfaces the disconnect.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
