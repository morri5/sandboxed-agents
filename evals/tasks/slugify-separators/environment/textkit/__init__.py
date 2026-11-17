"""Small text helpers."""

import re
import unicodedata


def slugify(text: str, sep: str = "-") -> str:
    """A lowercase ASCII slug of `text`: its words joined by `sep`."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]", sep, text)
    return text.lower()
