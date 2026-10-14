"""A client-side rate limiter over a sliding window of request times (ADR 0007, decision 3).

The limiter starts with one policy, a number of requests in a window of seconds, and
adds the policies that responses state in their ``x-ratelimit-policy`` header. A
request goes out only when every policy allows it, so a header can only make the
limiter stricter. After HTTP 429 no request goes out until the time that the source
asked for, or for a whole window when it gave none. A call waits at most ``max_wait``
seconds for a free slot, across all the requests it makes.

The limiter counts the requests of one process. Several servers that share a cache
each keep their own count.
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
# A source states one policy in practice; more than this many windows are ignored,
# so a stream of odd headers cannot make each check slow.
MAX_POLICIES = 8
# A longer Retry-After is cut to an hour, so a wrong header cannot stop the server for
# good. Digits beyond these are not read, since Python refuses to parse very long ones.
MAX_PAUSE = 3600
MAX_PAUSE_DIGITS = 9


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
        self.max_wait = max_wait
        self._policies: dict[float, int] = {float(window): limit}
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = anyio.Lock()

    @property
    def policies(self) -> tuple[tuple[int, float], ...]:
        """Every policy that the limiter keeps, as requests and seconds, shortest window first."""
        return tuple((limit, window) for window, limit in sorted(self._policies.items()))

    def deadline(self) -> float:
        """Return the latest time on the limiter's clock that a call starting now may wait to."""
        return self._clock() + self.max_wait

    async def acquire(self, deadline: float | None = None) -> None:
        """Wait for a free slot and take it, or raise LimitReachedError.

        A call that sends several requests passes the same ``deadline`` to each, so
        that together they wait no longer than ``max_wait``.
        """
        if deadline is None:
            deadline = self.deadline()
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
        """Add the policy that a response states, and back off after HTTP 429."""
        policy = POLICY.fullmatch(exchange.header("x-ratelimit-policy") or "")
        if policy is not None:
            limit, window = int(policy[1]), float(policy[2])
            known = self._policies.get(window)
            if limit >= 1 and window > 0:
                if known is not None:
                    self._policies[window] = min(known, limit)
                elif len(self._policies) < MAX_POLICIES:
                    self._policies[window] = limit
        if exchange.status == HTTPStatus.TOO_MANY_REQUESTS:
            pause = retry_after(exchange)
            pause = max(self._policies) if pause is None else pause
            self._blocked_until = max(self._blocked_until, self._clock() + pause)

    def _wait(self, now: float) -> float:
        """Return the seconds until a request may go, or zero or less if it may go now."""
        longest = max(self._policies)
        while self._sent and self._sent[0] <= now - longest:
            self._sent.popleft()
        wait = self._blocked_until - now
        sent = list(self._sent)
        for window, limit in self._policies.items():
            recent = [moment for moment in sent if moment > now - window]
            if len(recent) >= limit:
                wait = max(wait, recent[len(recent) - limit] + window - now)
        return wait


def retry_after(exchange: Exchange) -> int | None:
    """Return the seconds in a Retry-After header, at most an hour, or None without a number."""
    value = exchange.header("retry-after")
    if value is None or not value.isascii() or not value.isdigit():
        return None
    if len(value) > MAX_PAUSE_DIGITS:
        return MAX_PAUSE
    return min(int(value), MAX_PAUSE)
