# calc

A tiny calculator package, used as a fixture by the lab. One test fails on purpose: `average` uses integer division.

Run the tests with the standard library only (the agent's VM has no network, so nothing can be installed):

```sh
python -m unittest -v
```
