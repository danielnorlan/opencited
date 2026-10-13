"""A client-side rate limiter: a sliding window of request times (ADR 0007, decision 3).

The limiter starts at a limit per window and only ever lowers it, when a response's
rate limit headers state a lower rate. After HTTP 429 it lets no request through until
the time that the source asked for, or a whole window when the source gave none. A
request waits for a free slot at most ``max_wait`` seconds.
"""

from __future__ import annotations

import math
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from http import HTTPStatus

import anyio

from opencited.http import Exchange

# For example "40;w=60s": 40 requests in a window of 60 seconds.
POLICY = re.compile(r"\s*([0-9]{1,9})\s*;\s*w\s*=\s*([0-9]{1,9})\s*s?\s*")


class LimitReachedError(Exception):
    """No slot was free in time. ``wait`` is the seconds until the next one, rounded up."""

    def __init__(self, wait: int) -> None:
        super().__init__(f"the next free slot is {wait} s away")
        self.wait = wait


class RateLimiter:
    """Lets at most ``limit`` requests through in any ``window`` seconds.

    ``clock`` and ``sleep`` let tests run the limiter without waiting.
    """

    def __init__(
        self,
        limit: int = 30,
        window: float = 60.0,
        *,
        max_wait: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        if limit < 1 or window <= 0:
            raise ValueError("a rate limit needs at least one request in a positive window")
        self.limit = limit
        self.window = window
        self.max_wait = max_wait
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = anyio.Lock()

    async def acquire(self) -> None:
        """Wait for a free slot and take it, or raise LimitReachedError."""
        deadline = self._clock() + self.max_wait
        # Callers take slots in the order they arrive, so a waiting call is not
        # overtaken by a later one.
        async with self._lock:
            while True:
                now = self._clock()
                wait = self._wait(now)
                if wait <= 0:
                    self._sent.append(now)
                    return
                if now + wait > deadline:
                    raise LimitReachedError(math.ceil(wait))
                await self._sleep(wait)

    def observe(self, exchange: Exchange) -> None:
        """Lower the limit to what the response's headers state, and back off after 429."""
        policy = POLICY.fullmatch(exchange.header("x-ratelimit-policy") or "")
        if policy is not None:
            limit, window = int(policy[1]), int(policy[2])
            # Compare the rates, and keep the stricter one with its own window.
            if window > 0 and limit >= 1 and limit * self.window < self.limit * window:
                self.limit, self.window = limit, float(window)
        if exchange.status == HTTPStatus.TOO_MANY_REQUESTS:
            pause = retry_after(exchange)
            pause = self.window if pause is None else pause
            self._blocked_until = max(self._blocked_until, self._clock() + pause)

    def _wait(self, now: float) -> float:
        """Return the seconds until a request may go, or zero or less if it may go now."""
        while self._sent and self._sent[0] <= now - self.window:
            self._sent.popleft()
        wait = self._blocked_until - now
        if len(self._sent) >= self.limit:
            wait = max(wait, self._sent[len(self._sent) - self.limit] + self.window - now)
        return wait


def retry_after(exchange: Exchange) -> int | None:
    """Return the seconds in a Retry-After header, or None when it holds no such number."""
    value = exchange.header("retry-after")
    if value is not None and value.isascii() and value.isdigit():
        return int(value)
    return None
