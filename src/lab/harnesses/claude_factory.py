"""The Claude Agent SDK in the VM with DeepSeek, set up to run command prompts that
delegate to subagents (for a software factory that drives one stage per message).

The VM is `claude_agent`'s (DeepSeek only, host-scoped key, SDK mount). What the
runner does differently for this harness:

- The `Agent` tool is allowed, with the subagents defined in the task, parsed from
  the agent files in `FACTORY_AGENTS_DIR`. Their tool calls are recorded and
  counted against the cap like the main agent's, with `parent` set to the `Agent`
  call they belong to, so delegating hides no work.
- `setting_sources=[]`: no settings, hooks, CLAUDE.md, commands or agents are
  loaded from the repo (untrusted) or from a home directory.
- Agent `model: sonnet|opus|haiku` aliases all resolve to the DeepSeek model.

There is no native resume: every message starts a new SDK session, so a message
with `fresh_context` sees nothing of earlier ones but the working tree. The
message's `Limits` override the configured tool-call cap and budget.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from microsandbox import Sandbox

from lab import config, pricing
from lab.events import AgentEvent, Limits, Message
from lab.harnesses.claude_agent import ClaudeAgentHarness
from lab.sandbox import REPO_DIR, SandboxError, VmSpec, exec_runner

SYSTEM_PROMPT = f"""You work on a git repository at {REPO_DIR}, inside an isolated Linux VM
with Python 3.13 and git. The network only reaches the model API, so nothing can be
installed. No human is available during this run: never wait for input."""


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """The `key: value` lines between the leading `---` lines, and the body after them."""
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return {}, text
    meta = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip()
    return meta, body


def load_agents(directory: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """`AgentDefinition` fields per agent name from `directory/*.md`, and the sha256
    of those files (names and contents, in name order)."""
    agents: dict[str, dict[str, Any]] = {}
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.md")):
        text = path.read_text()
        digest.update(path.name.encode() + b"\0" + text.encode() + b"\0")
        meta, body = _frontmatter(text)
        tools = meta.get("tools")
        agents[meta.get("name") or path.stem] = {
            "description": meta.get("description", ""),
            "prompt": body.strip(),
            "tools": [t.strip() for t in tools.split(",") if t.strip()] if tools else None,
            "model": meta.get("model"),
        }
    return agents, digest.hexdigest()


class ClaudeFactoryHarness:
    name = "claude_factory"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use", "cost_report", "budget", "limits"})
    model = config.DEEPSEEK_MODEL

    def vm_spec(self) -> VmSpec:
        return ClaudeAgentHarness().vm_spec()

    async def run(
        self,
        transcript: list[Message],
        sandbox: Sandbox,
        run_id: str,
        resume: str | None = None,
        limits: Limits | None = None,
    ) -> AsyncIterator[AgentEvent]:
        agents, agents_sha = load_agents(config.FACTORY_AGENTS_DIR)
        if not agents:
            raise SandboxError(f"no agent files in {config.FACTORY_AGENTS_DIR}")
        limits = limits or Limits()
        task = {
            "run_id": run_id,
            "harness": self.name,
            "transcript": [asdict(m) for m in transcript],
            "resume": None,
            "system_prompt": SYSTEM_PROMPT,
            "model": config.DEEPSEEK_MODEL,
            "price": pricing.PRICES[config.DEEPSEEK_MODEL],
            "budget_usd": limits.max_usd or config.MAX_USD_PER_RUN,
            "max_tool_calls": limits.max_tool_calls or config.MAX_TOOL_CALLS,
            "agents": agents,
            "agents_sha": agents_sha,
        }
        async for event in exec_runner(sandbox, task):
            yield event
