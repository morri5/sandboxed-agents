"""Shared code reaches a harness only through the registry, never by importing it."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "lab"
# `lab.harnesses` (the registry) and `lab.harnesses.base` (the interface) are allowed.
HARNESS_IMPORT = re.compile(r"^\s*(from|import)\s+lab\.harnesses\.(?!base\b)\w+", re.MULTILINE)


def test_no_harness_module_is_imported_outside_the_harnesses_package() -> None:
    offenders = [
        f"{path.relative_to(SRC)}: {match.group(0).strip()}"
        for path in SRC.rglob("*.py")
        if "harnesses" not in path.relative_to(SRC).parts
        for match in HARNESS_IMPORT.finditer(path.read_text())
    ]
    assert offenders == []


def test_the_check_finds_a_direct_import() -> None:
    assert HARNESS_IMPORT.search("from lab.harnesses.claude_agent import ClaudeAgentHarness")
    assert not HARNESS_IMPORT.search("from lab.harnesses import HARNESSES")
    assert not HARNESS_IMPORT.search("from lab.harnesses.base import Harness")
