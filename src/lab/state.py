"""Explicit state machines for a session, its sandbox and its current run.

Each machine is a table from event type to the moves it allows, `{from: to}`.
`fold` runs the three tables over a session's log in order: an event a machine
does not list leaves that machine alone, and a listed event in a state the table
does not allow raises `IllegalTransition`. State is never stored separately; it
is always this fold over the log.

    session: created -> ready -> running -> idle -> closed, plus failed
    sandbox: none -> provisioning -> attached -> lost -> destroyed
    run:     queued -> running -> finished | failed | interrupted

A first run starts straight from `created` (the VM is provisioned inside the run);
`ready` is a session whose VM exists but has not run yet, as after a fork. A
`harness_switched` is only allowed between runs.
"""

from dataclasses import dataclass, field

from lab import config
from lab.events import Event, Limits, Message

RUN_ENDS = {"run_finished": "finished", "run_failed": "failed", "run_interrupted": "interrupted"}
OPEN = ("created", "ready", "idle")  # session states that accept a message, a fork or a close

SESSION: dict[str, dict[str, str]] = {
    "session_created": {"none": "created"},
    "forked_from": dict.fromkeys(OPEN, "created"),
    "sandbox_attached": {"created": "ready", **{s: s for s in ("ready", "running", "idle")}},
    "user_message": {s: s for s in OPEN},  # only an open session between runs takes one
    "harness_switched": {s: s for s in OPEN},
    "run_started": dict.fromkeys(OPEN, "running"),
    **{end: {"running": "idle"} for end in RUN_ENDS},
    "session_failed": dict.fromkeys(OPEN, "failed"),
    "session_closed": dict.fromkeys((*OPEN, "failed"), "closed"),
}

SANDBOX: dict[str, dict[str, str]] = {
    "sandbox_provisioning": dict.fromkeys(("none", "lost", "destroyed"), "provisioning"),
    "sandbox_attached": {"provisioning": "attached"},
    "sandbox_lost": dict.fromkeys(("provisioning", "attached"), "lost"),
    "sandbox_destroyed": dict.fromkeys(("none", "provisioning", "attached", "lost"), "destroyed"),
    # The copied history describes the source's VM; the fork starts without one.
    "forked_from": dict.fromkeys(("none", "attached", "lost"), "none"),
}

RUN: dict[str, dict[str, str]] = {
    "user_message": dict.fromkeys(("none", *RUN_ENDS.values()), "queued"),
    "run_started": {"queued": "running", "interrupted": "running"},  # from interrupted: a re-run
    **{end: {"running": state} for end, state in RUN_ENDS.items()},
    "forked_from": {s: s for s in ("none", "queued", *RUN_ENDS.values())},  # never mid-run
}

MACHINES = (("session", SESSION), ("sandbox", SANDBOX), ("run", RUN))


class IllegalTransition(Exception):
    """An event arrived in a state its machine does not allow it in."""


@dataclass
class SessionState:
    session: str = "none"
    sandbox: str = "none"
    run: str = "none"
    session_id: str | None = None
    repo_url: str | None = None
    harness: str | None = None  # the harness the next run uses
    sandbox_name: str | None = None
    sandbox_harness: str | None = None  # the harness the VM was made for (its VmSpec)
    commit: str | None = None  # the commit an https clone was taken at (None for a fixture)
    workspace: str | None = None  # last saved workspace patch, base64 (see `workspace_saved`)
    message_seq: int | None = None  # the last user message
    # Options of the last user message: run it without the earlier conversation (and
    # without a native resume), and with these limits instead of `config`'s.
    fresh_context: bool = False
    limits: Limits | None = None
    attempts: int = 0  # runs started for the last user message
    answered: bool = False  # the last user message got an `agent_message`
    run_id: str | None = None  # the last run started
    run_harness: str | None = None
    # The harness's own session to continue (`native_resume`): set by a finished run,
    # dropped by anything that changes the VM or the harness.
    resume: str | None = None
    last_seq: int = 0
    transcript: list[Message] = field(default_factory=list)

    @property
    def run_timeout_s(self) -> int:
        """Wall-time limit of a run for the last user message."""
        return (self.limits and self.limits.run_timeout_s) or config.RUN_TIMEOUT_S


def fold(events: list[Event]) -> SessionState:
    state = SessionState()
    for event in events:
        for attr, table in MACHINES:
            moves = table.get(event.type)
            if moves is None:
                continue
            current = getattr(state, attr)
            if current not in moves:
                raise IllegalTransition(
                    f"{attr}: {event.type} (seq {event.seq}) is not allowed in state {current}"
                )
            setattr(state, attr, moves[current])
        state.last_seq = event.seq
        p = event.payload
        match event.type:
            case "session_created":
                state.session_id = str(event.session_id)
                state.repo_url = p["repo_url"]
                state.harness = p["harness"]
            case "user_message":
                state.transcript.append(Message("user", p["text"]))
                state.message_seq = event.seq
                state.fresh_context = p.get("fresh_context", False)
                state.limits = Limits(**p["limits"]) if p.get("limits") else None
                state.attempts = 0
                state.answered = False
            case "agent_message":
                state.transcript.append(Message("assistant", p["text"]))
                state.answered = True
            case "harness_switched":
                state.harness = p["to"]
                state.resume = None
            case "sandbox_provisioning":
                state.sandbox_name = p["name"]
                state.resume = None
            case "sandbox_attached":
                state.sandbox_name = p["name"]
                state.sandbox_harness = p.get("harness", state.harness)  # older logs lack it
                state.commit = p.get("commit")
            case "sandbox_lost" | "sandbox_destroyed" | "forked_from":
                state.resume = None
            case "run_started":
                state.run_id = p["run_id"]
                state.run_harness = p["harness"]
                state.attempts += 1
                state.resume = None
            case "run_finished":
                state.resume = p.get("resume")
            case "workspace_saved":
                state.workspace = p["patch_b64"]
    return state
