"""Architecture B: the Claude Agent SDK runs inside the VM, DeepSeek is the brain.

The whole harness lives in the VM: `runner.py` drives a `ClaudeSDKClient` (and
with it the bundled Claude Code CLI and its own tools) in the repo, as a managed
job, so it keeps working if the API dies. The brain is DeepSeek's
Anthropic-compatible API. The VM's network allows DNS and `api.deepseek.com`
only, and the key never enters it: the guest sees a placeholder, which
microsandbox's host proxy swaps for the real `DEEPSEEK_API_KEY` on requests to
that host and no other.

With `LAB_MODEL_GATEWAY` set, the brain is reached through the Langfuse AI gateway
on the host instead, which records every model call (subagents included) as a
generation. The VM then reaches only the gateway's host port, its placeholder is
swapped for `LAB_GATEWAY_KEY` on requests to the gateway host, and the DeepSeek
key stays in Langfuse. The gateway speaks plain HTTP on the host, so this secret
is substituted without TLS: the traffic never leaves the machine.

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
from urllib.parse import urlsplit

from microsandbox import (
    DestGroup,
    Destination,
    Network,
    NetworkPolicy,
    Protocol,
    Rule,
    Sandbox,
    Secret,
    SecretEntry,
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


def sdk_model() -> str:
    """The model ID the SDK sends: the gateway's `<connection>:<model>`, or DeepSeek's own.
    Prices stay keyed by `config.DEEPSEEK_MODEL` either way."""
    if config.MODEL_GATEWAY:
        return f"{config.GATEWAY_CONNECTION}:{config.DEEPSEEK_MODEL}"
    return config.DEEPSEEK_MODEL


def _gateway_network() -> tuple[Network, list[SecretEntry], str]:
    """The VM's network, key and base URL for the brain behind `LAB_MODEL_GATEWAY`."""
    key = os.environ.get("LAB_GATEWAY_KEY")
    if not key:
        raise SandboxError("LAB_GATEWAY_KEY is not set on the host")
    url = urlsplit(config.MODEL_GATEWAY)
    if not url.hostname or not url.port:
        raise SandboxError(f"LAB_MODEL_GATEWAY needs a host and a port: {config.MODEL_GATEWAY}")
    secret = Secret.env(
        "ANTHROPIC_AUTH_TOKEN",
        value=key,
        allow=[url.hostname],
        require_tls_identity=False,  # the gateway listens on plain HTTP on the host
        substitution=SecretSubstitution(header_fields=("authorization",)),
    )
    port = Rule.allow(
        destination=Destination.group(DestGroup.HOST), protocol=Protocol.TCP, port=url.port
    )
    network = Network(policy=NetworkPolicy(rules=(*Rule.allow_dns(), port)))
    return network, [secret], config.MODEL_GATEWAY


def _deepseek_network() -> tuple[Network, list[SecretEntry], str]:
    """The VM's network, key and base URL for DeepSeek's own API."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SandboxError("DEEPSEEK_API_KEY is not set on the host")
    substitution = SecretSubstitution(header_fields=("authorization", "x-api-key"))
    secrets = [
        Secret.env(var, value=key, allow=[DEEPSEEK_HOST], substitution=substitution)
        for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
    ]
    allow = Rule.allow(destination=Destination.domain(DEEPSEEK_HOST))
    network = Network(policy=NetworkPolicy(rules=(*Rule.allow_dns(), allow)))
    return network, secrets, f"https://{DEEPSEEK_HOST}/anthropic"


class ClaudeAgentHarness:
    name = "claude_agent"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use", "native_resume", "cost_report", "budget"})
    model = config.DEEPSEEK_MODEL

    def vm_spec(self) -> VmSpec:
        network, secrets, base_url = (
            _gateway_network() if config.MODEL_GATEWAY else _deepseek_network()
        )
        if not SDK_HOST.is_dir():
            raise SandboxError(f"no Claude Agent SDK in {SDK_HOST}: run make claude-sdk")
        return VmSpec(
            network=network,
            memory_mib=VM_MEMORY_MIB,
            env={
                "ANTHROPIC_BASE_URL": base_url,
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
            "model": sdk_model(),
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
