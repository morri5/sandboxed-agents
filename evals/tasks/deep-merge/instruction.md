`merge` in the `confmerge` package is too shallow, and it changes its first argument:

```python
defaults = {"db": {"host": "localhost", "port": 5432}, "debug": False}
merge(defaults, {"db": {"port": 6543}})
```

returns `{"db": {"port": 6543}, "debug": False}` and modifies `defaults`. It should return `{"db": {"host": "localhost", "port": 6543}, "debug": False}`.

Fix it: when both sides hold a dict under the same key, merge them recursively, at any depth. In every other case the value from `override` wins (lists are replaced, not concatenated, and a dict replaces a non-dict and the other way round). Neither argument may be modified, and the result must not share any dict with the arguments, so that changing the result later cannot change them either.
