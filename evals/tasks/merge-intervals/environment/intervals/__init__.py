"""Helpers for closed intervals given as (start, end) tuples with start <= end."""


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping intervals; the result is sorted by start."""
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def total_length(intervals: list[tuple[int, int]]) -> int:
    """The length covered by the intervals, counting overlaps once."""
    return sum(end - start for start, end in merge_intervals(intervals))
