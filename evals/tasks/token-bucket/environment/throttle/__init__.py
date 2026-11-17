"""A token-bucket rate limiter with an injectable clock."""

import time
from collections.abc import Callable


class TokenBucket:
    """Starts full with `capacity` tokens and refills at `rate` tokens per second."""

    def __init__(
        self, rate: float, capacity: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rate = rate
        self.capacity = capacity
        self.clock = clock
        self.tokens = capacity
        self.started = clock()

    def _refill(self) -> None:
        self.tokens += (self.clock() - self.started) * self.rate

    def allow(self, cost: float = 1) -> bool:
        """Take `cost` tokens if there are enough; True if the request may go ahead."""
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
