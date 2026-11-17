"""Invoice numbers: INV-<year>-<four digits>, counting per year."""

import re

PATTERN = re.compile(r"^INV-(\d{4})-(\d{4})$")


def next_number(existing: list[str], year: int) -> str:
    used = [int(m.group(2)) for n in existing if (m := PATTERN.match(n)) and int(m.group(1)) == year]
    return f"INV-{year}-{max(used, default=0) + 1:04d}"
