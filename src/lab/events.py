"""The append-only event log: append, read, and fork (the fold lives in `state.py`).

One writer per session: an append takes a transaction-scoped advisory try-lock on
the session and raises `ConcurrentWriterError` if another transaction holds it,
so a second writer fails loudly instead of waiting. Under the lock the next seq
is `max(seq) + 1`; the primary key `(session_id, seq)` is the last guard.
"""

import json
from dataclasses import dataclass
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
        await _lock(conn, session_id)
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
        await _notify(conn, session_id, seq)
        return seq


async def _lock(conn: Any, session_id: UUID) -> None:
    if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", lock_key(session_id)):
        raise ConcurrentWriterError(f"session {session_id} has another writer")


async def _notify(conn: Any, session_id: UUID, seq: int) -> None:
    # Only the address goes through NOTIFY (payload limit 8000 bytes); listeners read the row.
    note = json.dumps({"session_id": str(session_id), "seq": seq})
    await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, note)


async def fork(pool: asyncpg.Pool, source: UUID, target: UUID, at_seq: int) -> int:
    """Start session `target` as a copy of `source` up to `at_seq`, plus `forked_from`.

    One transaction: a fork never exists without its `forked_from`, which tells the
    fold that the copied VM history belongs to the source. Returns its seq.
    """
    async with pool.acquire() as conn, conn.transaction():
        await _lock(conn, target)
        await conn.execute(
            """
            INSERT INTO events (session_id, seq, type, payload, idem_key, created_at)
            SELECT $2, seq, type, payload, idem_key, created_at FROM events
            WHERE session_id = $1 AND seq <= $3
            """,
            source,
            target,
            at_seq,
        )
        seq = at_seq + 1
        payload = {"session_id": str(source), "at_seq": at_seq}
        await conn.execute(
            "INSERT INTO events (session_id, seq, type, payload) VALUES ($1, $2, $3, $4)",
            target,
            seq,
            "forked_from",
            payload,
        )
        await _notify(conn, target, seq)
        return seq


async def read(pool: asyncpg.Pool, session_id: UUID, after_seq: int = 0) -> list[Event]:
    rows = await pool.fetch(
        "SELECT * FROM events WHERE session_id = $1 AND seq > $2 ORDER BY seq",
        session_id,
        after_seq,
    )
    return [Event(**dict(row)) for row in rows]


async def open_sessions(pool: asyncpg.Pool) -> list[UUID]:
    """Every session that is neither closed nor failed, oldest first."""
    rows = await pool.fetch(
        """
        SELECT session_id FROM events GROUP BY session_id
        HAVING NOT bool_or(type IN ('session_closed', 'session_failed'))
        ORDER BY session_id
        """
    )
    return [row["session_id"] for row in rows]
