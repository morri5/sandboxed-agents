`wrap` in the `wordwrap` package returns lines that are too long. `wrap("the quick brown fox", 9)` returns `["the quick", "brown fox"]`, which is right, but `wrap("aaa bbb ccc", 6)` returns `["aaa bbb", "ccc"]`, and `"aaa bbb"` has 7 characters.

Fix it so that no line is ever longer than `width`:

- Words are separated by any whitespace; lines join words with a single space, greedily (as many words as fit).
- A word longer than `width` is split into pieces of exactly `width` characters, plus a shorter last piece; the pieces are placed like separate words.
- `width` below 1 raises `ValueError`. Empty or blank text gives `[]`.
