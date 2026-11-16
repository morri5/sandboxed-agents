import uuid
from datetime import UTC, datetime

import pytest

from lab.events import Event, Message
from lab.recovery import needs_run
from lab.state import IllegalTransition, fold

Item = tuple[str, dict[str, object]]

CREATED: Item = ("session_created", {"repo_url": "https://example.org/r", "harness": "scripted"})
MESSAGE: Item = ("user_message", {"text": "hi"})
STARTED: Item = ("run_started", {"run_id": "r1", "harness": "scripted"})
PROVISIONING: Item = ("sandbox_provisioning", {"name": "lab-x"})
ATTACHED: Item = ("sandbox_attached", {"name": "lab-x", "commit": "abc"})
ANSWER: Item = ("agent_message", {"text": "hello"})
FINISHED: Item = ("run_finished", {})
INTERRUPTED: Item = ("run_interrupted", {})
SWITCHED: Item = ("harness_switched", {"from": "scripted", "to": "claude_agent"})


def _log(*items: Item) -> list[Event]:
    sid = uuid.UUID(int=1)
    now = datetime.now(UTC)
    return [Event(sid, seq, t, p, None, now) for seq, (t, p) in enumerate(items, start=1)]


def test_fold_a_first_run() -> None:
    state = fold(_log(CREATED, MESSAGE, STARTED, PROVISIONING, ATTACHED, ("tool_call", {})))
    assert (state.session, state.sandbox, state.run) == ("running", "attached", "running")
    assert (state.run_id, state.sandbox_name, state.commit, state.last_seq) == (
        "r1",
        "lab-x",
        "abc",
        6,
    )
    log = _log(CREATED, MESSAGE, STARTED, PROVISIONING, ATTACHED, ANSWER, FINISHED)
    state = fold(log)
    assert (state.session, state.run, state.answered, state.attempts) == (
        "idle",
        "finished",
        True,
        1,
    )
    assert state.transcript == [Message("user", "hi"), Message("assistant", "hello")]
    assert fold(log) == state  # deterministic


@pytest.mark.parametrize(
    "items",
    [
        (MESSAGE,),  # before the session exists
        (CREATED, STARTED),  # a run without a message
        (CREATED, MESSAGE, STARTED, STARTED),  # two runs at once
        (CREATED, MESSAGE, STARTED, MESSAGE),  # a message while a run is going
        (CREATED, ATTACHED),  # attached without provisioning
        (CREATED, FINISHED),  # a run that never started
        (CREATED, MESSAGE, STARTED, ("forked_from", {})),  # a fork in the middle of a run
        (CREATED, ("session_closed", {}), MESSAGE),  # a message after the close
        (CREATED, MESSAGE, STARTED, SWITCHED),  # a harness switch in the middle of a run
    ],
)
def test_illegal_transitions_raise(items: tuple[Item, ...]) -> None:
    with pytest.raises(IllegalTransition):
        fold(_log(*items))


def test_fork_starts_without_a_vm() -> None:
    history = (CREATED, MESSAGE, STARTED, PROVISIONING, ATTACHED, FINISHED)
    state = fold(_log(*history, ("forked_from", {"session_id": "s", "at_seq": 6})))
    assert (state.session, state.sandbox, state.run) == ("created", "none", "finished")
    assert state.commit == "abc"  # a rebuilt VM starts from the same commit
    own_vm: tuple[Item, ...] = (
        ("sandbox_provisioning", {"name": "lab-y"}),
        ("sandbox_attached", {"name": "lab-y"}),
    )
    assert fold(_log(*history, ("forked_from", {}), *own_vm)).session == "ready"


def test_needs_run() -> None:
    assert needs_run(fold(_log(CREATED, MESSAGE)))  # never started
    assert needs_run(fold(_log(CREATED, MESSAGE, STARTED, INTERRUPTED)))  # first interruption
    twice = (CREATED, MESSAGE, STARTED, INTERRUPTED, STARTED, INTERRUPTED)
    assert not needs_run(fold(_log(*twice)))  # at most one re-run per message
    assert not needs_run(fold(_log(CREATED, MESSAGE, STARTED, ANSWER, INTERRUPTED)))  # answered
    assert not needs_run(fold(_log(CREATED, MESSAGE, STARTED, FINISHED)))
    assert not needs_run(fold(_log(CREATED, MESSAGE, STARTED)))  # still running


def test_harness_switch_and_native_resume() -> None:
    finished: Item = ("run_finished", {"resume": "sdk-1"})
    first_run = (CREATED, MESSAGE, STARTED, PROVISIONING, ATTACHED, ANSWER, finished)
    state = fold(_log(*first_run))
    assert (state.harness, state.sandbox_harness, state.resume) == ("scripted", "scripted", "sdk-1")
    # The next run may resume; a new run, a switch or another VM drops the token.
    assert fold(_log(*first_run, ("workspace_saved", {"patch_b64": ""}))).resume == "sdk-1"
    assert fold(_log(*first_run, MESSAGE, STARTED)).resume is None
    assert fold(_log(*first_run, ("sandbox_lost", {}))).resume is None
    switched = fold(_log(*first_run, SWITCHED))
    assert (switched.harness, switched.sandbox_harness, switched.resume) == (
        "claude_agent",
        "scripted",  # the VM is still the old harness's until the next run replaces it
        None,
    )
    assert switched.session == "idle"
