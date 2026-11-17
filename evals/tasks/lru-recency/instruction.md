The `LRUCache` in the `lrucache` package evicts the wrong entries. With capacity 2:

```python
cache.put("a", 1)
cache.put("b", 2)
cache.get("a")
cache.put("c", 3)
```

evicts `"a"`, although `"b"` is the least recently used entry. Fix it: a `get` that finds its key, and a `put` of a key that is already in the cache (which updates its value), both make that key the most recently used. A `get` of a missing key changes nothing. `keys()` lists keys from least to most recently used.
