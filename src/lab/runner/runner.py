"""Runs inside the VM. Reads one JSON task on stdin, writes JSONL events.

Every durable event gets the run id and a per-run counter `rseq`, is appended to
`<runs_dir>/<run_id>.jsonl` and flushed first, and only then printed on stdout.
The file is what survives if the API that reads stdout dies. Live `agent_delta`
tokens go to stdout only.

Standard library only: the VM image has Python but none of the lab's packages.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

OUTPUT_LIMIT = 16_000  # characters kept per tool output (the tail, where summaries are)


class Emitter:
    def __init__(self, run_id: str, log: IO[str]) -> None:
        self.run_id = run_id
        self.log = log
        self.rseq = 0

    def event(self, type: str, payload: dict[str, Any]) -> None:
        self.rseq += 1
        line = json.dumps(
            {"run_id": self.run_id, "rseq": self.rseq, "type": type, "payload": payload}
        )
        self.log.write(line + "\n")
        self.log.flush()
        print(line, flush=True)

    def delta(self, text: str) -> None:
        line = json.dumps({"run_id": self.run_id, "type": "agent_delta", "payload": {"text": text}})
        print(line, flush=True)


def tail(text: str) -> str:
    return text[-OUTPUT_LIMIT:]


def shell(emit: Emitter, call_id: str, command: str, timeout: float = 600) -> dict[str, Any]:
    emit.event("tool_call", {"id": call_id, "name": "shell", "args": {"command": command}})
    try:
        proc = subprocess.run(
            ["sh", "-c", command], capture_output=True, text=True, timeout=timeout
        )
        result = {
            "stdout": tail(proc.stdout),
            "stderr": tail(proc.stderr),
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        result = {"stdout": "", "stderr": f"timed out after {timeout} s", "exit_code": -1}
    emit.event("tool_result", {"id": call_id, "name": "shell", **result})
    return result


def read_file(emit: Emitter, call_id: str, path: str) -> str:
    emit.event("tool_call", {"id": call_id, "name": "read_file", "args": {"path": path}})
    content = tail(Path(path).read_text(errors="replace"))
    emit.event("tool_result", {"id": call_id, "name": "read_file", "content": content})
    return content


def scripted(emit: Emitter, task: dict[str, Any]) -> None:
    """A deterministic fake agent: look around, read a file, run the tests, answer."""
    question = next((m["text"] for m in reversed(task["transcript"]) if m["role"] == "user"), "")
    delay = task.get("step_delay_s", 0)  # slows the steps down, for the crash tests
    time.sleep(delay)
    listing = shell(emit, "call-1", "ls -A")["stdout"].split()
    readmes = sorted(name for name in listing if name.lower().startswith("readme"))
    readme = readmes[0] if readmes else None
    if readme:
        time.sleep(delay)
        read_file(emit, "call-2", readme)
    time.sleep(delay)
    shell(
        emit,
        "call-3",
        "python -m pip install -q --disable-pip-version-check --root-user-action=ignore"
        " pytest -e .",
    )
    time.sleep(delay)
    tests = shell(emit, "call-4", "python -m pytest -q")
    lines = tests["stdout"].strip().splitlines()
    summary = lines[-1] if lines else "no output"
    answer = (
        f'You asked: "{question}". The repo has {len(listing)} top-level entries'
        + (f", I read {readme}" if readme else "")
        + f", and pytest exited {tests['exit_code']}: {summary}"
    )
    for word in answer.split(" "):
        emit.delta(word + " ")
    emit.event("agent_message", {"text": answer})


HARNESSES = {"scripted": scripted}


def main() -> None:
    # The run id also arrives as argv[1], only so the API can find this job again.
    task = json.load(sys.stdin)
    runs = Path(task["runs_dir"])
    runs.mkdir(parents=True, exist_ok=True)
    with (runs / f"{task['run_id']}.jsonl").open("a") as log:
        emit = Emitter(task["run_id"], log)
        try:
            HARNESSES[task["harness"]](emit, task)
        except Exception as exc:  # report any agent failure as an event, then exit non-zero
            emit.event("run_failed", {"reason": "runner_error", "error": repr(exc)})
            sys.exit(1)
        emit.event("run_finished", {})


if __name__ == "__main__":
    main()
