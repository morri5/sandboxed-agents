"""Greedy word wrapping for plain text."""


def wrap(text: str, width: int) -> list[str]:
    """Split `text` into lines of at most `width` characters, breaking at spaces."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + len(word) + 1 <= width + 1:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
