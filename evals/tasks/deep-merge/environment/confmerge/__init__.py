"""Merge configuration dictionaries, such as defaults with user settings."""

from typing import Any


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`base` with the values of `override` on top."""
    result = base
    result.update(override)
    return result
