"""Limits and model settings, read from the environment once at import."""

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# VM size for every session VM (microsandbox defaults: 1 vCPU, 512 MiB).
VM_CPUS = _int("LAB_VM_CPUS", 1)
VM_MEMORY_MIB = _int("LAB_VM_MEMORY_MIB", 512)

# Per-run limits. Exceeding one ends the run with `run_failed{reason}`.
RUN_TIMEOUT_S = _int("LAB_RUN_TIMEOUT_S", 900)  # wall time of one run: reason "timeout"
EXEC_TIMEOUT_S = _int("LAB_EXEC_TIMEOUT_S", 120)  # one command in the VM (reported to the model)
MAX_TOOL_CALLS = _int("LAB_MAX_TOOL_CALLS", 50)  # per run: reason "max_tool_calls"

# Brain of the host-placement harness: Ollama, which stays bound to loopback.
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = os.environ.get("LAB_OLLAMA_MODEL", "gemma4:26b-a4b-it-mtp-q4_K_M")
OLLAMA_NUM_CTX = _int("LAB_OLLAMA_NUM_CTX", 65536)  # Ollama advises 32k+ for tool calling

# Pause before each step of the scripted fake agent, so crash tests can kill the API mid-run.
SCRIPTED_STEP_DELAY_S = _int("LAB_SCRIPTED_STEP_DELAY_S", 0)

# Brain of the in-VM `claude_agent` harness: DeepSeek's Anthropic-compatible API.
DEEPSEEK_MODEL = os.environ.get("LAB_DEEPSEEK_MODEL", "deepseek-flash")
# Spend limit of one `claude_agent` run, from token usage and `pricing.py`: reason "budget".
MAX_USD_PER_RUN = float(os.environ.get("LAB_MAX_USD_PER_RUN", "0.50"))
# Optional: the Langfuse AI gateway in front of DeepSeek, as the VM reaches it (for example
# `http://host.microsandbox.internal:3031/anthropic`). Unset, the VM calls DeepSeek directly.
MODEL_GATEWAY = os.environ.get("LAB_MODEL_GATEWAY", "")
# The gateway's DeepSeek connection: its model IDs are `<connection>:<model>`.
GATEWAY_CONNECTION = os.environ.get("LAB_GATEWAY_CONNECTION", "deepseek")

# Subagents of the `claude_factory` harness: every `*.md` here (Claude Code agent files,
# frontmatter plus prompt) becomes one. Read on every run, never mounted into the VM.
FACTORY_AGENTS_DIR = Path(os.environ.get("FACTORY_AGENTS_DIR", "~/.claude/agents")).expanduser()
