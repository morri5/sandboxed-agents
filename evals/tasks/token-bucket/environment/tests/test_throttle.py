import unittest

from throttle import TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TokenBucketTest(unittest.TestCase):
    def test_burst_up_to_capacity(self) -> None:
        bucket = TokenBucket(rate=1, capacity=3, clock=Clock())
        self.assertEqual([bucket.allow() for _ in range(4)], [True, True, True, False])

    def test_cost_larger_than_capacity_is_refused(self) -> None:
        bucket = TokenBucket(rate=1, capacity=2, clock=Clock())
        self.assertFalse(bucket.allow(cost=5))


if __name__ == "__main__":
    unittest.main()
