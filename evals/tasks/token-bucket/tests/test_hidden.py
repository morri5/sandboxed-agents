from throttle import TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def test_steady_rate_after_a_burst() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=1, capacity=3, clock=clock)
    assert [bucket.allow() for _ in range(3)] == [True] * 3
    allowed = []
    for _ in range(10):
        clock.now += 0.5
        allowed.append(bucket.allow())
    assert allowed == [False, True] * 5


def test_never_more_than_capacity() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=2, capacity=4, clock=clock)
    clock.now += 1000
    assert [bucket.allow() for _ in range(5)] == [True, True, True, True, False]


def test_refused_request_takes_nothing() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=1, capacity=2, clock=clock)
    assert bucket.allow(2)
    clock.now += 1
    assert not bucket.allow(2)
    clock.now += 1
    assert bucket.allow(2)


def test_fractional_rate() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=0.25, capacity=1, clock=clock)
    assert bucket.allow()
    clock.now += 3
    assert not bucket.allow()
    clock.now += 1
    assert bucket.allow()
