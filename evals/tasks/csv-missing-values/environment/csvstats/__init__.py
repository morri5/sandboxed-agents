"""Summary statistics for columns of CSV data read with csv.DictReader."""

import csv
import io


def read_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _number(cell: str) -> float:
    return float(cell) if cell else 0.0


def column_mean(rows: list[dict[str, str]], column: str) -> float | None:
    """The mean of the numeric values in `column`."""
    values = [_number(row[column]) for row in rows]
    return sum(values) / len(values)


def column_max(rows: list[dict[str, str]], column: str) -> float | None:
    """The largest numeric value in `column`."""
    return max(_number(row[column]) for row in rows)
