"""Durations as short human-readable strings, such as "1h 30m"."""

UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def format_duration(seconds: int) -> str:
    """`seconds` (a non-negative int) as its non-zero parts, largest unit first: 3725 -> "1h 2m 5s"."""
    if seconds < 0:
        raise ValueError("negative duration")
    if seconds == 0:
        return "0s"
    parts = []
    for unit, size in UNITS:
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return " ".join(parts)
