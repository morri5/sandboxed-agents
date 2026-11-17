"""Compare semantic version strings such as "1.4.2" or "2.0.0-rc.1"."""


def compare_versions(a: str, b: str) -> int:
    """-1 if a < b, 0 if they are equal, 1 if a > b."""
    left, right = a.split("."), b.split(".")
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def latest(versions: list[str]) -> str:
    """The highest version in a non-empty list."""
    best = versions[0]
    for version in versions[1:]:
        if compare_versions(version, best) > 0:
            best = version
    return best
