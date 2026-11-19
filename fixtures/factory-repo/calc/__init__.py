"""Small arithmetic helpers."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def average(values: list[float]) -> float:
    """Arithmetic mean of a non-empty list."""
    if not values:
        raise ValueError("average() of an empty list")
    return sum(values) // len(values)
