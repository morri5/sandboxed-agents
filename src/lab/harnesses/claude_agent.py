"""Architecture B: the Claude Agent SDK runs inside the VM, DeepSeek is the brain.

The whole harness lives in the VM: `runner.py` drives a `ClaudeSDKClient` (and
with it the bundled Claude Code CLI and its own tools) in the repo, as a managed
job, so it keeps working if the API dies. The brain is DeepSeek's
Anthropic-compatible API. The VM's network allows DNS and `api.deepseek.com`
only, and the key never enters it: the guest sees a placeholder, which
microsandbox's host proxy swaps for the real `DEEPSEEK_API_KEY` on requests to
that host and no other.

The SDK (with its CLI) is installed once on the host for Linux arm64, into
`.cache/` (`make claude-sdk`), and mounted read-only into every VM of this harness.
"""

import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from microsandbox import (
    Destination,
    Network,
    NetworkPolicy,
    Rule,
    Sandbox,
    Secret,
    SecretSubstitution,
    Volume,
)

from lab import config, pricing
from lab.events import AgentEvent, Message
from lab.sandbox import REPO_DIR, SandboxError, VmSpec, exec_runner

SDK_VERSION = "0.2.182"
SDK_HOST = Path(__file__).resolve().parents[3] / ".cache" / f"claude-agent-sdk-{SDK_VERSION}"
SDK_GUEST = "/opt/claude-sdk"
DEEPSEEK_HOST = "api.deepseek.com"
VM_MEMORY_MIB = 1024  # Anthropic's hosting guide asks for 1 GiB per agent

SYSTEM_PROMPT = f"""You work on a git repository at {REPO_DIR}, inside an isolated Linux VM
with Python 3.13 and git. The network only reaches the model API, so nothing can be
installed. Investigate before you change code, run the tests to check your work, and
finish with a short answer that says what you found or changed."""


class ClaudeAgentHarness:
    name = "claude_agent"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use", "native_resume", "cost_report", "budget"})
    model = config.DEEPSEEK_MODEL

    def vm_spec(self) -> VmSpec:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise SandboxError("DEEPSEEK_API_KEY is not set on the host")
        if not SDK_HOST.is_dir():
            raise SandboxError(f"no Claude Agent SDK in {SDK_HOST}: run make claude-sdk")
        substitution = SecretSubstitution(header_fields=("authorization", "x-api-key"))
        secrets = [
            Secret.env(var, value=key, allow=[DEEPSEEK_HOST], substitution=substitution)
            for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
        ]
        allow = Rule.allow(destination=Destination.domain(DEEPSEEK_HOST))
        return VmSpec(
            network=Network(policy=NetworkPolicy(rules=(*Rule.allow_dns(), allow))),
            memory_mib=VM_MEMORY_MIB,
            env={
                "ANTHROPIC_BASE_URL": f"https://{DEEPSEEK_HOST}/anthropic",
                "PYTHONPATH": SDK_GUEST,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
            },
            secrets=secrets,
            volumes={SDK_GUEST: Volume.bind(str(SDK_HOST), readonly=True)},
        )

    async def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str, resume: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        task = {
            "run_id": run_id,
            "harness": self.name,
            "transcript": [asdict(m) for m in transcript],
            "resume": resume,
            "system_prompt": SYSTEM_PROMPT,
            "model": config.DEEPSEEK_MODEL,
            "price": pricing.PRICES[config.DEEPSEEK_MODEL],
            "budget_usd": config.MAX_USD_PER_RUN,
            "max_tool_calls": config.MAX_TOOL_CALLS,
        }
        async for event in exec_runner(sandbox, task):
            yield event


def install_sdk() -> None:
    """Install the SDK for the VMs (Linux arm64, Python 3.13) into `SDK_HOST`, once."""
    if SDK_HOST.is_dir():
        return
    tmp = SDK_HOST.with_name(f"{SDK_HOST.name}.tmp-{os.getpid()}")
    subprocess.run(
        ["uv", "pip", "install", "-q", "--target", str(tmp), "--python-version", "3.13",
         "--python-platform", "aarch64-manylinux_2_17", "--only-binary", ":all:",
         f"claude-agent-sdk=={SDK_VERSION}"],
        check=True,
    )  # fmt: skip
    try:
        tmp.rename(SDK_HOST)
    except OSError:  # another install finished first
        shutil.rmtree(tmp)
