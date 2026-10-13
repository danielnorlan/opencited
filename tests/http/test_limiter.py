"""The rate limiter, on a clock that the tests move instead of waiting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import anyio
import pytest
from recorded import Json, description

from opencited.http import Exchange
from opencited.http.limiter import LimitReachedError, RateLimiter


class Clock:
    """A monotonic clock whose sleep moves it forward at once."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await anyio.lowlevel.checkpoint()


def limiter(
    clock: Clock, *, limit: int = 30, window: float = 60.0, max_wait: float = 10.0
) -> RateLimiter:
    return RateLimiter(limit, window, max_wait=max_wait, clock=clock, sleep=clock.sleep)


def acquire(limiter: RateLimiter, times: int = 1) -> None:
    async def run() -> None:
        for _ in range(times):
            await limiter.acquire()

    asyncio.run(run())


def exchange(status: int = 200, headers: tuple[tuple[str, str], ...] = ()) -> Exchange:
    return Exchange(
        method="GET",
        url="https://data.ssb.no/api/pxwebapi/v2/config",
        body=None,
        status=status,
        headers=headers,
        content=b"{}",
        retrieved_at=datetime.now(UTC),
    )


def recorded_headers(name: str) -> tuple[tuple[str, str], ...]:
    response = cast("Json", description(name)["response"])
    return tuple(cast("dict[str, str]", response["headers"]).items())


def test_thirty_requests_go_at_once() -> None:
    clock = Clock()
    acquire(limiter(clock), 30)
    assert clock.sleeps == []


def test_the_next_request_waits_until_the_oldest_leaves_the_window() -> None:
    clock = Clock()
    rate = limiter(clock)
    acquire(rate, 30)
    clock.now = 55.0
    acquire(rate)
    assert clock.sleeps == [5.0]


def test_a_wait_beyond_the_maximum_fails_with_the_wait_rounded_up() -> None:
    clock = Clock()
    rate = limiter(clock)
    acquire(rate, 30)
    clock.now = 40.5
    with pytest.raises(LimitReachedError) as caught:
        acquire(rate)
    assert caught.value.wait == 20
    assert clock.sleeps == []


def test_the_recorded_rate_limit_header_keeps_the_stricter_limit_of_thirty() -> None:
    headers = recorded_headers("config")
    assert dict(headers)["x-ratelimit-policy"] == "40;w=60s"
    rate = limiter(Clock())
    rate.observe(exchange(headers=headers))
    assert (rate.limit, rate.window) == (30, 60.0)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("20;w=60s", (20, 60.0)),
        ("1;w=10s", (1, 10.0)),
        ("5;w=1s", (30, 60.0)),
        ("30;w=60s", (30, 60.0)),
        ("0;w=60s", (30, 60.0)),
        ("20;w=0s", (30, 60.0)),
        ("twenty", (30, 60.0)),
    ],
)
def test_a_header_only_ever_lowers_the_rate(policy: str, expected: tuple[int, float]) -> None:
    rate = limiter(Clock())
    rate.observe(exchange(headers=(("x-ratelimit-policy", policy),)))
    assert (rate.limit, rate.window) == expected


def test_a_lower_limit_from_a_header_holds_requests_back() -> None:
    clock = Clock()
    rate = limiter(clock, max_wait=60.0)
    rate.observe(exchange(headers=(("x-ratelimit-policy", "2;w=60s"),)))
    acquire(rate, 3)
    assert clock.sleeps == [60.0]


def test_http_429_holds_every_request_for_the_time_the_source_asks() -> None:
    clock = Clock()
    rate = limiter(clock)
    rate.observe(exchange(429, (("retry-after", "30"),)))
    with pytest.raises(LimitReachedError) as caught:
        acquire(rate)
    assert caught.value.wait == 30
    clock.now = 30.0
    acquire(rate)
    assert clock.sleeps == []


def test_http_429_without_a_wait_holds_requests_for_a_window() -> None:
    clock = Clock()
    rate = limiter(clock, max_wait=100.0)
    rate.observe(exchange(429))
    acquire(rate)
    assert clock.sleeps == [60.0]


def test_concurrent_requests_never_exceed_the_limit() -> None:
    clock = Clock()
    rate = limiter(clock, limit=2, window=10.0, max_wait=100.0)
    taken: list[float] = []

    async def one() -> None:
        await rate.acquire()
        taken.append(clock.now)

    async def run() -> None:
        async with anyio.create_task_group() as tasks:
            for _ in range(7):
                tasks.start_soon(one)

    asyncio.run(run())
    assert len(taken) == 7
    assert all(sum(start <= time < start + 10 for time in taken) <= 2 for start in taken)


@pytest.mark.parametrize(("limit", "window"), [(0, 60.0), (30, 0.0), (30, -1.0)])
def test_a_limit_needs_a_request_in_a_positive_window(limit: int, window: float) -> None:
    with pytest.raises(ValueError, match="positive window"):
        RateLimiter(limit, window)
