"""The rate limiter, on a clock that the tests move instead of waiting.

SSB's recorded responses all state `40;w=60s`. The other header values here stand for
no response from SSB: they check how the limiter reads a policy that a source could
state, which cannot be recorded on demand.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import anyio
import pytest
from recorded import Json, description

from opencited.http import Exchange, make_client
from opencited.http.limiter import MAX_PAUSE, MAX_POLICIES, LimitReachedError, RateLimiter
from opencited.sources.ssb import BASE_URL, SsbClient


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


def policy(value: str) -> Exchange:
    return exchange(headers=(("x-ratelimit-policy", value),))


def recorded_headers(name: str) -> tuple[tuple[str, str], ...]:
    response = cast("Json", description(name)["response"])
    return tuple(cast("dict[str, str]", response["headers"]).items())


def test_the_defaults_are_ssb_s_limit_and_a_wait_of_ten_seconds() -> None:
    assert RateLimiter().policies == ((30, 60.0),)
    assert RateLimiter().max_wait == 10.0

    async def client_limiter() -> RateLimiter:
        async with make_client(BASE_URL) as http:
            return SsbClient(http).limiter

    default = asyncio.run(client_limiter())
    assert default.policies == ((30, 60.0),)
    assert default.max_wait == 10.0


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


def test_the_requests_of_one_call_share_one_wait() -> None:
    clock = Clock()
    rate = limiter(clock, limit=1, window=6.0)
    acquire(rate)

    async def two_requests() -> None:
        deadline = rate.deadline()
        await rate.acquire(deadline)
        await rate.acquire(deadline)

    with pytest.raises(LimitReachedError):
        asyncio.run(two_requests())
    assert clock.sleeps == [6.0]


def test_the_recorded_rate_limit_header_keeps_the_limit_of_thirty() -> None:
    headers = recorded_headers("config")
    assert dict(headers)["x-ratelimit-policy"] == "40;w=60s"
    rate = limiter(Clock())
    rate.observe(exchange(headers=headers))
    assert rate.policies == ((30, 60.0),)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20;w=60s", ((20, 60.0),)),
        ("20;w=30s", ((20, 30.0), (30, 60.0))),
        ("100;w=3600s", ((30, 60.0), (100, 3600.0))),
        ("0;w=60s", ((30, 60.0),)),
        ("20;w=0s", ((30, 60.0),)),
        ("twenty", ((30, 60.0),)),
    ],
)
def test_a_header_adds_its_policy(value: str, expected: tuple[tuple[int, float], ...]) -> None:
    rate = limiter(Clock())
    rate.observe(policy(value))
    assert rate.policies == expected


def test_every_policy_holds_requests_back() -> None:
    clock = Clock()
    rate = limiter(clock, max_wait=60.0)
    # 40 a minute, but at most 20 in any 30 seconds.
    rate.observe(policy("20;w=30s"))
    acquire(rate, 21)
    assert clock.sleeps == [30.0]


def test_the_number_of_policies_is_bounded() -> None:
    rate = limiter(Clock())
    for window in range(1, 20):
        rate.observe(policy(f"1000;w={window}s"))
    assert len(rate.policies) == MAX_POLICIES


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


@pytest.mark.parametrize("seconds", ["86400000", "9" * 5000])
def test_a_long_retry_after_is_cut_to_an_hour(seconds: str) -> None:
    rate = limiter(Clock())
    rate.observe(exchange(429, (("retry-after", seconds),)))
    with pytest.raises(LimitReachedError) as caught:
        acquire(rate)
    assert caught.value.wait == MAX_PAUSE


def test_waiting_calls_take_slots_in_the_order_they_arrive() -> None:
    clock = Clock()
    rate = limiter(clock, limit=1, window=10.0, max_wait=100.0)
    order: list[str] = []

    async def call(name: str) -> None:
        await rate.acquire()
        order.append(name)

    async def run() -> None:
        async with anyio.create_task_group() as tasks:
            for name in ("first", "second", "third"):
                tasks.start_soon(call, name)

    asyncio.run(run())
    assert order == ["first", "second", "third"]
    assert clock.now == 20.0


@pytest.mark.parametrize(("limit", "window"), [(0, 60.0), (30, 0.0), (30, -1.0)])
def test_a_limit_needs_a_request_in_a_positive_window(limit: int, window: float) -> None:
    with pytest.raises(ValueError, match="positive window"):
        RateLimiter(limit, window)
