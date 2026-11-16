"""The append-only event log: append, read, and fold into session state.

One writer per session: an append takes a transaction-scoped advisory try-lock on
the session and raises `ConcurrentWriterError` if another transaction holds it,
so a second writer fails loudly instead of waiting. Under the lock the next seq
is `max(seq) + 1`; the primary key `(session_id, seq)` is the last guard.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

CHANNEL = "events"
TERMINAL_RUN_EVENTS = frozenset({"run_finished", "run_failed", "run_interrupted"})


class ConcurrentWriterError(Exception):
    """Another transaction is appending to the same session right now."""


@dataclass(frozen=True)
class Event:
    session_id: UUID
    seq: int
    type: str
    payload: dict[str, Any]
    idem_key: str | None
    created_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "seq": self.seq,
            "type": self.type,
            "payload": self.payload,
            "idem_key": self.idem_key,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class AgentEvent:
    """One event produced by a harness during a run.

    `rseq` is the per-run counter of durable events (None for live `agent_delta`
    tokens). The session layer appends durable events with idem_key `<run_id>:<rseq>`.
    """

    type: str
    payload: dict[str, Any]
    run_id: str
    rseq: int | None = None


@dataclass
class SessionState:
    session_id: UUID | None = None
    repo_url: str | None = None
    harness: str | None = None
    sandbox: str | None = None  # name of the attached microsandbox VM
    run_id: str | None = None  # the run in progress, if any
    status: str = "none"  # none, ready, running, idle
    last_seq: int = 0
    transcript: list[Message] = field(default_factory=list)


def lock_key(session_id: UUID) -> int:
    """64-bit advisory lock key from the uuid's last 8 bytes.

    Those are random in a uuidv7 (the first 8 hold the timestamp, so sessions
    created in the same millisecond would often share a key).
    """
    return int.from_bytes(session_id.bytes[8:], "big", signed=True)


async def append(
    pool: asyncpg.Pool,
    session_id: UUID,
    type: str,
    payload: dict[str, Any],
    idem_key: str | None = None,
) -> int:
    """Append one event and return its seq.

    With an `idem_key` that was already used in this session, nothing is written
    and the seq of the existing event is returned.
    """
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", lock_key(session_id)):
            raise ConcurrentWriterError(f"session {session_id} has another writer")
        seq: int | None = await conn.fetchval(
            """
            INSERT INTO events (session_id, seq, type, payload, idem_key)
            SELECT $1, coalesce(max(seq), 0) + 1, $2, $3, $4 FROM events WHERE session_id = $1
            ON CONFLICT (session_id, idem_key) DO NOTHING
            RETURNING seq
            """,
            session_id,
            type,
            payload,
            idem_key,
        )
        if seq is None:
            existing: int = await conn.fetchval(
                "SELECT seq FROM events WHERE session_id = $1 AND idem_key = $2",
                session_id,
                idem_key,
            )
            return existing
        # Only the address goes through NOTIFY (payload limit 8000 bytes); listeners read the row.
        note = json.dumps({"session_id": str(session_id), "seq": seq})
        await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, note)
        return seq


async def read(pool: asyncpg.Pool, session_id: UUID, after_seq: int = 0) -> list[Event]:
    rows = await pool.fetch(
        "SELECT * FROM events WHERE session_id = $1 AND seq > $2 ORDER BY seq",
        session_id,
        after_seq,
    )
    return [Event(**dict(row)) for row in rows]


def fold_state(events: list[Event]) -> SessionState:
    """Session state is a pure fold over the log; it is never stored separately."""
    state = SessionState()
    for event in events:
        state.last_seq = event.seq
        p = event.payload
        match event.type:
            case "session_created":
                state.session_id = event.session_id
                state.repo_url = p["repo_url"]
                state.harness = p["harness"]
                state.status = "ready"
            case "user_message":
                state.transcript.append(Message("user", p["text"]))
            case "agent_message":
                state.transcript.append(Message("assistant", p["text"]))
            case "sandbox_attached":
                state.sandbox = p["name"]
            case "run_started":
                state.run_id = p["run_id"]
                state.status = "running"
            case t if t in TERMINAL_RUN_EVENTS:
                state.run_id = None
                state.status = "idle"
    return state
