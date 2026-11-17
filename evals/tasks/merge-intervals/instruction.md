`merge_intervals` in the `intervals` package gives wrong results. `merge_intervals([(5, 7), (1, 3), (3, 4)])` returns `[(5, 7), (1, 3), (3, 4)]`, but it should return `[(1, 4), (5, 7)]`.

The input may come in any order, and intervals that only touch (one ends where the next starts) must be merged too. The result must be sorted by start, and the input list must not be modified. `total_length` should then be correct as well.
