"""Harness registry. Shared code looks harnesses up here by name, never imports them directly."""

from lab.harnesses.base import Harness
from lab.harnesses.deepagents_ollama import DeepAgentsOllamaHarness
from lab.harnesses.scripted import ScriptedHarness

HARNESSES: dict[str, Harness] = {h.name: h for h in (ScriptedHarness(), DeepAgentsOllamaHarness())}
