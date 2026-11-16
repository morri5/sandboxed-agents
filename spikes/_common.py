"""Helpers shared by the Phase 1 spikes."""

import json
import statistics
import subprocess
from pathlib import Path
from typing import Any

from microsandbox import MicrosandboxError, Sandbox

IMAGE = "python:3.13-slim"
OUT = Path(__file__).parent / "out"
MIB = 1024 * 1024


def ensure_home() -> None:
    """Workaround for microsandbox issue #2041 (ENOENT on a fresh home)."""
    (Path.home() / ".microsandbox" / "sandboxes").mkdir(parents=True, exist_ok=True)


async def cleanup(prefix: str) -> None:
    """Destroy every sandbox whose name starts with `prefix`, ignoring errors."""
    page = await Sandbox.list()
    for handle in page.sandboxes:
        if handle.name.startswith(prefix):
            try:
                await handle.destroy(force=True)
            except MicrosandboxError as exc:
                print(f"cleanup: {handle.name}: {exc}")


def ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def summary_ms(samples: list[float]) -> dict[str, float]:
    """p50, p95, min and max of samples given in seconds, reported in ms."""
    ordered = sorted(samples)
    p95 = ordered[max(0, round(0.95 * len(ordered)) - 1)]
    return {
        "n": len(ordered),
        "p50_ms": ms(statistics.median(ordered)),
        "p95_ms": ms(p95),
        "min_ms": ms(ordered[0]),
        "max_ms": ms(ordered[-1]),
    }


def msb_rss_bytes(name: str) -> int | None:
    """RSS of the host `msb machine` process that runs the named sandbox."""
    ps = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True)
    for line in ps.stdout.splitlines():
        rss, _, command = line.strip().partition(" ")
        if " machine " in command and f"--name {name} " in command:
            return int(rss) * 1024
    return None


def write_result(spike: str, result: dict[str, Any]) -> None:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{spike}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {path}")
