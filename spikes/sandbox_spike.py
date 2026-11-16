"""Spike: does microsandbox work on this Mac, and what does a VM cost?

Measures create latency (cold and warm), boot-to-exec latency, the `sb.exec`
round trip for a trivial command, a git clone plus test run inside a VM,
fork time, and host memory per VM for 1, 3 and 5 concurrent VMs.
"""

import asyncio
import time
from typing import Any

from _common import (
    IMAGE,
    MIB,
    cleanup,
    ensure_home,
    ms,
    msb_rss_bytes,
    summary_ms,
    write_result,
)
from microsandbox import Image, MicrosandboxError, Sandbox

PREFIX = "lab-spike-sb-"
REPO = "https://github.com/pallets/markupsafe"
EXEC_SAMPLES = 50
WARM_CREATES = 3


async def create_and_first_exec(name: str, memory: int = 512) -> tuple[Sandbox, float, float]:
    """Return the sandbox, create latency and boot-to-exec latency (seconds)."""
    t0 = time.perf_counter()
    sb = await Sandbox.create(name, image=IMAGE, memory=memory, replace=True)
    t1 = time.perf_counter()
    out = await sb.exec("true")
    t2 = time.perf_counter()
    assert out.exit_code == 0, out.stderr_text
    return sb, t1 - t0, t2 - t1


async def sh(sb: Sandbox, script: str, timeout: float = 300) -> tuple[str, float]:
    t0 = time.perf_counter()
    out = await sb.shell(script, timeout=timeout)
    elapsed = time.perf_counter() - t0
    if out.exit_code != 0:
        raise RuntimeError(f"{script!r} exited {out.exit_code}: {out.stderr_text[-2000:]}")
    return out.stdout_text, elapsed


async def measure_lifecycle() -> dict[str, Any]:
    result: dict[str, Any] = {}

    # Cold: drop the image from the local cache so the first create includes the pull.
    try:
        await Image.remove(IMAGE, force=True)
        result["cold_includes_image_pull"] = True
    except MicrosandboxError as exc:
        print(f"image not removed ({exc}); cold create runs with a cached image")
        result["cold_includes_image_pull"] = False
    sb, create_s, boot_s = await create_and_first_exec(f"{PREFIX}main")
    result["cold_create_ms"] = ms(create_s)
    result["cold_boot_to_exec_ms"] = ms(boot_s)
    print(f"cold create {ms(create_s)} ms, first exec {ms(boot_s)} ms")

    creates, boots = [], []
    for i in range(WARM_CREATES):
        warm, create_s, boot_s = await create_and_first_exec(f"{PREFIX}warm{i}")
        creates.append(create_s)
        boots.append(boot_s)
        await warm.destroy(force=True)
    result["warm_create"] = summary_ms(creates)
    result["warm_boot_to_exec"] = summary_ms(boots)
    print(f"warm create {result['warm_create']}, first exec {result['warm_boot_to_exec']}")

    rtts = []
    for _ in range(EXEC_SAMPLES):
        t0 = time.perf_counter()
        await sb.exec("true")
        rtts.append(time.perf_counter() - t0)
    result["exec_roundtrip_true"] = summary_ms(rtts)
    print(f"exec round trip {result['exec_roundtrip_true']}")

    # Fork the running VM: the fork must see the source's files, and writes must not leak back.
    await sh(sb, "mkdir -p /work && echo before-fork > /work/marker")
    t0 = time.perf_counter()
    fork = await sb.fork(f"{PREFIX}fork")
    fork_s = time.perf_counter() - t0
    fork_sees = await fork.exec("cat", ["/work/marker"])
    await fork.exec("touch", ["/work/only-in-fork"])
    source_sees = await sb.exec("test", ["-e", "/work/only-in-fork"])
    result["fork"] = {
        "fork_ms": ms(fork_s),
        "fork_sees_source_files": fork_sees.stdout_text.strip() == "before-fork",
        "fork_write_isolated": source_sees.exit_code != 0,
    }
    print(f"fork {result['fork']}")
    await fork.destroy(force=True)
    await sb.destroy(force=True)
    return result


async def clone_and_test() -> dict[str, Any]:
    """Install git and pytest, clone a small public repo and run its tests (needs egress)."""
    sb = await Sandbox.create(f"{PREFIX}clone", image=IMAGE, replace=True)
    _, apt_s = await sh(
        sb,
        "apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null"
        " && pip install -q pytest",
        timeout=600,
    )
    _, clone_s = await sh(sb, f"git clone -q --depth 1 {REPO} /work/repo")
    tests_out, tests_s = await sh(sb, "cd /work/repo && PYTHONPATH=src python -m pytest -q 2>&1")
    result = {
        "repo": REPO,
        "install_git_and_pytest_ms": ms(apt_s),
        "clone_ms": ms(clone_s),
        "pytest_ms": ms(tests_s),
        "pytest_summary": tests_out.strip().splitlines()[-1],
    }
    print(f"clone and test {result}")
    await sb.destroy(force=True)
    return result


async def measure_memory(memory: int, count: int) -> dict[str, Any]:
    names = [f"{PREFIX}mem{memory}-{i}" for i in range(count)]
    t0 = time.perf_counter()
    boxes = await asyncio.gather(*(create_and_first_exec(n, memory) for n in names))
    wall_s = time.perf_counter() - t0
    await asyncio.sleep(3)  # let the guests settle after boot
    resident, rss = [], []
    for (sb, _, _), name in zip(boxes, names, strict=True):
        metrics = await sb.metrics()
        if metrics.memory_host_resident_bytes is not None:
            resident.append(metrics.memory_host_resident_bytes / MIB)
        proc_rss = msb_rss_bytes(name)
        if proc_rss is not None:
            rss.append(proc_rss / MIB)
    for sb, _, _ in boxes:
        await sb.destroy(force=True)
    row = {
        "memory_limit_mib": memory,
        "vms": count,
        "concurrent_create_wall_ms": ms(wall_s),
        "guest_resident_mib_mean": round(sum(resident) / len(resident), 1) if resident else None,
        "msb_process_rss_mib_mean": round(sum(rss) / len(rss), 1) if rss else None,
        "msb_process_rss_mib_max": round(max(rss), 1) if rss else None,
    }
    print(f"memory {row}")
    return row


async def main() -> None:
    ensure_home()
    await cleanup(PREFIX)
    try:
        result = await measure_lifecycle()
        result["memory_idle_after_boot"] = [
            await measure_memory(memory, count) for memory in (512, 1024) for count in (1, 3, 5)
        ]
        write_result("sandbox_spike", result)  # keep the numbers even if the clone step fails
        result["clone_and_test"] = await clone_and_test()
    finally:
        await cleanup(PREFIX)
    write_result("sandbox_spike", result)


if __name__ == "__main__":
    asyncio.run(main())
