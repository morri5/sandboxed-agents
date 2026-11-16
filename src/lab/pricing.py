"""DeepSeek prices, for the cost and budget of the `claude_agent` harness.

The Agent SDK's own `total_cost_usd` and `max_budget_usd` assume Claude prices,
so they are ignored. Prices are USD per million tokens at DeepSeek's peak rate
(off-peak is half), so a cost or a budget check errs on the high side. Source:
https://api-docs.deepseek.com/quick_start/pricing
"""

from typing import Any

from lab.runner.runner import usd

CHECKED = "2026-11-16"

# `input` is a cache miss (also used for tokens written to the cache), `cache_read` a hit.
PRICES: dict[str, dict[str, float]] = {
    "deepseek-flash": {"input": 0.30, "cache_read": 0.006, "output": 1.20},
    "deepseek-v4-pro": {"input": 1.32, "cache_read": 0.044, "output": 3.96},
}


def cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Cost of an Anthropic-style `usage` (input, cache read and write, output tokens)."""
    return usd(usage, PRICES[model])
