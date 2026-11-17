In the `csvstats` package, missing values are counted as zero. For the CSV

```
city,temp
Oslo,4
Rome,
Cairo,29
```

`column_mean(rows, "temp")` returns `11.0`, but it should return `16.5`.

Fix `column_mean` and `column_max`: cells that are empty or only whitespace are missing and must be skipped (other cells may have surrounding spaces, such as `" 7 "`). If a column has no values at all, both functions return `None` instead of failing. A value that is not a number still raises `ValueError`.
