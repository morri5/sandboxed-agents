"""Spike: does a VM survive a SIGKILL of the Python process that created it?

For each case an owner process creates a VM, starts a loop in it that appends a
tick to a file every second, and is then killed with SIGKILL. A fresh process
reattaches with `Sandbox.get(name)` + `connect()` and checks whether the loop
kept running.

Cases:
  detached   `detached=True` and `detach()` before the owner dies
  attached   defaults (no `detached`, no `detach()`)
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from _common import IMAGE, cleanup, ensure_home, ms, msb_rss_bytes, write_result
from microsandbox import MicrosandboxError, Sandbox

PREFIX = "lab-spike-re-"
TICKS = "/root/ticks"
LOOP = f"while true; do date +%s >> {TICKS}; sleep 1; done"


async def owner(name: str, detached: bool) -> None:
    sb = await Sandbox.create(name, image=IMAGE, replace=True, detached=detached)
    await sb.exec_detached("sh", ["-c", LOOP])
    if detached:
        await sb.detach()
    print("READY", flush=True)
    await asyncio.Event().wait()  # block until SIGKILL


async def count_ticks(sb: Sandbox) -> int:
    out = await sb.exec("wc", ["-l", TICKS])
    return int(out.stdout_text.split()[0]) if out.exit_code == 0 else -1


async def reattach(name: str) -> None:
    result: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        handle = await Sandbox.get(name)
    except MicrosandboxError as exc:
        print(json.dumps({"found": False, "error": repr(exc)}))
        return
    result["found"] = True
    result["status_after_kill"] = str(handle.status)
    try:
        sb = await handle.connect(timeout=10)
    except MicrosandboxError as exc:
        result["connected"] = False
        result["connect_error"] = repr(exc)
        print(json.dumps(result))
        return
    result["connected"] = True
    result["reattach_ms"] = ms(time.perf_counter() - t0)
    first = await count_ticks(sb)
    await asyncio.sleep(3)
    second = await count_ticks(sb)
    result["ticks_at_reattach"] = first
    result["ticks_3s_later"] = second
    result["loop_still_running"] = second > first > 0
    print(json.dumps(result))


def run_case(case: str) -> dict[str, Any]:
    name = f"{PREFIX}{case}"
    proc = subprocess.Popen(
        [sys.executable, __file__, "owner", name, case],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    assert line == "READY", f"owner did not start: {line!r}"
    time.sleep(3)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    time.sleep(5)
    result: dict[str, Any] = {
        "case": case,
        "msb_process_alive_5s_after_kill": msb_rss_bytes(name) is not None,
    }
    out = subprocess.run(
        [sys.executable, __file__, "reattach", name], capture_output=True, text=True, timeout=120
    )
    if out.returncode != 0:
        result["reattach_error"] = out.stderr[-2000:]
    else:
        result.update(json.loads(out.stdout.strip().splitlines()[-1]))
    print(f"{case}: {result}")
    return result


async def main() -> None:
    ensure_home()
    await cleanup(PREFIX)
    try:
        cases = [await asyncio.to_thread(run_case, case) for case in ("detached", "attached")]
    finally:
        await cleanup(PREFIX)
    write_result("reattach_spike", {"cases": cases})
    detached = cases[0]
    assert detached.get("loop_still_running"), "detached VM did not survive its owner"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "owner":
        asyncio.run(owner(sys.argv[2], detached=sys.argv[3] == "detached"))
    elif len(sys.argv) > 1 and sys.argv[1] == "reattach":
        asyncio.run(reattach(sys.argv[2]))
    else:
        asyncio.run(main())
