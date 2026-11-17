"""A small least-recently-used cache."""

from collections import OrderedDict
from typing import Any


class LRUCache:
    """Holds at most `capacity` entries; when full, `put` evicts the least recently used one."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._data: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        return self._data.get(key, default)

    def put(self, key: Any, value: Any) -> None:
        self._data[key] = value
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def keys(self) -> list[Any]:
        """Keys from least to most recently used."""
        return list(self._data)

    def __len__(self) -> int:
        return len(self._data)
