"""Export formats.

Each format lives in a module of this package and registers its function with
`@register("<name>", "<file suffix>")`; the function takes a sequence of invoices
and returns the exported text. `_MODULES` lists the modules loaded at import,
which is what makes a format available: the CLI's `--format` choices come from
`available_formats()`, and `--output <file>` picks the format by its suffix
(`format_for`).
"""

import importlib
from collections.abc import Callable, Sequence
from pathlib import Path

from ledger.models import Invoice

Exporter = Callable[[Sequence[Invoice]], str]

_REGISTRY: dict[str, Exporter] = {}
_SUFFIXES: dict[str, str] = {}  # file suffix -> format name
_MODULES = ("csv_export", "json_export", "text_export")


class UnknownFormat(KeyError):
    pass


def register(name: str, suffix: str) -> Callable[[Exporter], Exporter]:
    def decorator(function: Exporter) -> Exporter:
        if name in _REGISTRY or suffix in _SUFFIXES:
            raise ValueError(f"export format {name!r} or suffix {suffix!r} registered twice")
        _REGISTRY[name] = function
        _SUFFIXES[suffix] = name
        return function

    return decorator


def get_exporter(name: str) -> Exporter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownFormat(name) from None


def available_formats() -> list[str]:
    return sorted(_REGISTRY)


def format_for(path: Path) -> str:
    """The format registered for the file's suffix."""
    try:
        return _SUFFIXES[path.suffix.lower()]
    except KeyError:
        raise UnknownFormat(f"no export format for {path.name}") from None


for _module in _MODULES:
    importlib.import_module(f"{__name__}.{_module}")
