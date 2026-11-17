"""Timestamps and calendar months."""

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeError(ValueError):
    pass


def parse_timestamp(text: str) -> datetime:
    """An ISO 8601 timestamp with an offset; a naive one is refused."""
    try:
        value = datetime.fromisoformat(text.strip())
    except ValueError:
        raise TimeError(f"not an ISO 8601 timestamp: {text!r}") from None
    if value.tzinfo is None:
        raise TimeError(f"timestamp without an offset: {text!r}")
    return value


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TimeError(f"unknown time zone: {name!r}") from None


def parse_month(text: str) -> tuple[int, int]:
    """`2026-03` -> (2026, 3)."""
    try:
        year, month = (int(part) for part in text.split("-"))
    except ValueError:
        raise TimeError(f"not a month (YYYY-MM): {text!r}") from None
    if not 1 <= month <= 12:
        raise TimeError(f"not a month (YYYY-MM): {text!r}")
    return year, month


def month_range(year: int, month: int, tz: tzinfo) -> tuple[datetime, datetime]:
    """The UTC instants where the calendar month starts and ends in `tz` (end exclusive)."""
    start = datetime(year, month, 1)
    end = datetime(year + month // 12, month % 12 + 1, 1)
    offset = tz.utcoffset(start) or timedelta(0)
    return (start - offset).replace(tzinfo=UTC), (end - offset).replace(tzinfo=UTC)


def to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)
