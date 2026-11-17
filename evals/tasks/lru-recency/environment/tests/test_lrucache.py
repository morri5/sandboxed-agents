import unittest

from lrucache import LRUCache


class LRUCacheTest(unittest.TestCase):
    def test_put_and_get(self) -> None:
        cache = LRUCache(2)
        cache.put("a", 1)
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("missing"))

    def test_evicts_oldest_when_full(self) -> None:
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        self.assertEqual(cache.keys(), ["b", "c"])


if __name__ == "__main__":
    unittest.main()
