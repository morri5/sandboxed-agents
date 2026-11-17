from lrucache import LRUCache


def test_get_refreshes_recency() -> None:
    cache = LRUCache(3)
    for key in "xyz":
        cache.put(key, key.upper())
    assert cache.get("x") == "X"
    cache.put("w", "W")
    assert cache.keys() == ["z", "x", "w"]


def test_put_of_existing_key_refreshes_and_updates() -> None:
    cache = LRUCache(2)
    cache.put("p", 1)
    cache.put("q", 2)
    cache.put("p", 10)
    cache.put("r", 3)
    assert cache.keys() == ["p", "r"]
    assert cache.get("p") == 10


def test_get_of_missing_key_changes_nothing() -> None:
    cache = LRUCache(2)
    cache.put(1, "one")
    cache.put(2, "two")
    assert cache.get(3, "none") == "none"
    assert cache.keys() == [1, 2]


def test_capacity_one() -> None:
    cache = LRUCache(1)
    cache.put("k", 1)
    cache.put("k", 2)
    assert len(cache) == 1
    assert cache.get("k") == 2
