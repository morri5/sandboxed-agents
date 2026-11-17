"""Export formats.

Each format lives in a module of this package and registers its function with
`@register("<name>")`; the function takes a sequence of invoices and returns the
exported text. `_MODULES` lists the modules loaded at import, which is what makes
a format available (the CLI's `--format` choices come from `available_formats()`).
"""

import importlib
from collections.abc import Callable, Sequence

from ledger.models import Invoice

Exporter = Callable[[Sequence[Invoice]], str]

_REGISTRY: dict[str, Exporter] = {}
_MODULES = ("csv_export", "json_export", "text_export")


class UnknownFormat(KeyError):
    pass


def register(name: str) -> Callable[[Exporter], Exporter]:
    def decorator(function: Exporter) -> Exporter:
        if name in _REGISTRY:
            raise ValueError(f"export format {name!r} registered twice")
        _REGISTRY[name] = function
        return function

    return decorator


def get_exporter(name: str) -> Exporter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownFormat(name) from None


def available_formats() -> list[str]:
    return sorted(_REGISTRY)


for _module in _MODULES:
    importlib.import_module(f"{__name__}.{_module}")
