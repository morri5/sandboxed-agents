"""Harness registry. Shared code looks harnesses up here by name, never imports them directly."""

from lab.harnesses.base import Harness
from lab.harnesses.claude_agent import ClaudeAgentHarness
from lab.harnesses.claude_agent_local import BRIDGE, ClaudeAgentLocalHarness
from lab.harnesses.deepagents_ollama import DeepAgentsOllamaHarness
from lab.harnesses.scripted import ScriptedHarness

HARNESSES: dict[str, Harness] = {
    h.name: h
    for h in (
        ScriptedHarness(),
        DeepAgentsOllamaHarness(),
        ClaudeAgentHarness(),
        ClaudeAgentLocalHarness(),
    )
}


async def start_host_services() -> None:
    """What harnesses need on the host for as long as the API runs (the Ollama
    bridge of `claude_agent_local`, which in-VM runs reach over vsock)."""
    await BRIDGE.start()


async def stop_host_services() -> None:
    await BRIDGE.stop()
