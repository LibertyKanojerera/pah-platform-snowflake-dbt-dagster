"""Rate limiting.

A token bucket rather than a fixed sleep between requests: it allows a short
burst up to `capacity` and then settles to `rate`, which matches how servers
actually meter. A fixed sleep wastes the headroom a server is willing to give
and still has no notion of a ceiling.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, *, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = rate if capacity is None else capacity
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until `tokens` are available. Returns seconds spent waiting.

        Uses a monotonic clock: a wall-clock jump, from NTP or a laptop
        resuming from sleep, would otherwise grant or withhold a burst of
        tokens that was never earned.
        """
        if tokens > self.capacity:
            raise ValueError(f"{tokens} exceeds bucket capacity {self.capacity}")

        slept = 0.0
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return slept

                wait = (tokens - self._tokens) / self.rate
                time.sleep(wait)
                slept += wait
