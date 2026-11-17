The `timefmt` package can format durations but not parse them. Add `parse_duration(text: str) -> int` to it, returning the number of seconds. For example `parse_duration("1h 30m")` returns `5400`.

Rules:

- The text is one or more parts `<digits><unit>`, with the units `d`, `h`, `m` and `s`. Spaces between parts are optional (`"1h30m"` is fine), and leading or trailing spaces are ignored.
- Each unit appears at most once, and units come in the order `d`, `h`, `m`, `s`.
- Anything else raises `ValueError`: an empty string, a number without a unit, an unknown unit, a sign, a decimal point, a repeated unit or units out of order.
- `parse_duration(format_duration(n)) == n` for every non-negative int `n`.
