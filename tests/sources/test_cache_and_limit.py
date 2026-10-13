"""The SSB client with a response cache and a rate limiter, over recorded responses.

Responses written by a test stand only for HTTP 429 and an unreachable source, which
cannot be recorded on demand.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from recorded import FIXTURES

from opencited.http import make_client
from opencited.http.cache import ResponseCache
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.sources import RateLimitedError, SourceUnavailableError
from opencited.sources.ssb import BASE_URL, SsbClient, cache_expiry

REPLAY = replay_transport(FIXTURES)
# Queries fetch no data in SSB's revision period, so the client's clock is set outside it.
NOON = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
# 06:00 in Oslo on the same day, in the revision period.
REVISION = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
FAR = datetime(2100, 1, 1, tzinfo=UTC)
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}


class Counter:
    """A transport that answers from ``answer`` and keeps the paths it was asked for."""

    def __init__(self, answer: Callable[[httpx2.Request], httpx2.Response] | None = None):
        self.paths: list[str] = []
        self.answer = answer or REPLAY.handle_request
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path.rsplit("/", 1)[-1])
        return self.answer(request)


def run[T](
    work: Callable[[SsbClient], Awaitable[T]],
    counter: Counter,
    *,
    cache: ResponseCache | None = None,
    limiter: RateLimiter | None = None,
    now: datetime = NOON,
    revision_period: bool = True,
) -> T:
    async def call() -> T:
        async with make_client(BASE_URL, transport=counter.transport) as http:
            client = SsbClient(
                http,
                now=lambda: now,
                cache=cache,
                limiter=limiter,
                revision_period=revision_period,
            )
            return await work(client)

    return asyncio.run(call())


def cache_in(tmp_path: Path, now: datetime = NOON) -> ResponseCache:
    """Return a cache whose clock agrees with the client's, so no test depends on today."""
    return ResponseCache(tmp_path / "responses.sqlite", now=lambda: now)


def test_metadata_comes_from_the_cache_the_second_time(tmp_path: Path) -> None:
    counter = Counter()
    cache = cache_in(tmp_path)
    first = run(lambda client: client.metadata("05810", "en"), counter, cache=cache)
    second = run(lambda client: client.metadata("05810", "en"), counter, cache=cache)
    assert second == first
    assert counter.paths == ["metadata"]


def test_a_cached_query_repeats_the_provenance_and_says_so(tmp_path: Path) -> None:
    counter = Counter()
    cache = cache_in(tmp_path)
    first = run(lambda client: client.query("05810", POPULATION, "en"), counter, cache=cache)
    second = run(lambda client: client.query("05810", POPULATION, "en"), counter, cache=cache)
    assert first.cached is False
    assert second.cached is True
    assert second.provenance == first.provenance
    assert second.observations == first.observations
    assert counter.paths == ["metadata", "data"]


def test_search_and_configuration_are_not_cached(tmp_path: Path) -> None:
    counter = Counter()
    cache = cache_in(tmp_path)

    async def twice(client: SsbClient) -> None:
        for _ in range(2):
            await client.search("population", "en")
            await client.config()

    run(twice, counter, cache=cache)
    assert counter.paths == ["tables", "config", "tables", "config"]


@pytest.mark.parametrize(
    ("revision_period", "paths"),
    [(True, ["metadata", "data", "data"]), (False, ["metadata", "data"])],
)
def test_no_data_from_the_revision_period_is_cached(
    tmp_path: Path, revision_period: bool, paths: list[str]
) -> None:
    counter = Counter()
    cache = cache_in(tmp_path, REVISION)

    async def twice(client: SsbClient) -> None:
        for _ in range(2):
            await client.metadata("05810", "en")
            await client.data("05810", POPULATION, "en")

    run(twice, counter, cache=cache, now=REVISION, revision_period=revision_period)
    assert counter.paths == paths


def test_an_expired_entry_is_fetched_again(tmp_path: Path) -> None:
    counter = Counter()
    run(lambda client: client.metadata("05810", "en"), counter, cache=cache_in(tmp_path))
    later = cache_in(tmp_path, FAR)
    run(lambda client: client.metadata("05810", "en"), counter, cache=later)
    assert counter.paths == ["metadata", "metadata"]


def test_an_unreachable_source_gives_an_error_and_never_stale_data(tmp_path: Path) -> None:
    run(lambda client: client.metadata("05810", "en"), Counter(), cache=cache_in(tmp_path))

    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    with pytest.raises(SourceUnavailableError, match="could not be reached"):
        run(
            lambda client: client.metadata("05810", "en"),
            Counter(down),
            cache=cache_in(tmp_path, FAR),
        )


@pytest.mark.parametrize(
    ("requested", "expires_at"),
    [
        # 08:00 in Oslo, summer time: the same day at 08:15.
        (datetime(2026, 10, 14, 6, 0, tzinfo=UTC), datetime(2026, 10, 14, 6, 15, tzinfo=UTC)),
        # 08:15 in Oslo: the next day.
        (datetime(2026, 10, 14, 6, 15, tzinfo=UTC), datetime(2026, 10, 15, 6, 15, tzinfo=UTC)),
        # Noon in Oslo.
        (datetime(2026, 10, 14, 10, 0, tzinfo=UTC), datetime(2026, 10, 15, 6, 15, tzinfo=UTC)),
        # The day before summer time ends on 25 October 2026.
        (datetime(2026, 10, 24, 12, 0, tzinfo=UTC), datetime(2026, 10, 25, 7, 15, tzinfo=UTC)),
        # The day before summer time starts on 28 March 2027.
        (datetime(2027, 3, 27, 12, 0, tzinfo=UTC), datetime(2027, 3, 28, 6, 15, tzinfo=UTC)),
    ],
)
def test_an_entry_lasts_until_the_next_0815_in_oslo(
    requested: datetime, expires_at: datetime
) -> None:
    assert cache_expiry(requested) == expires_at


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def strict_limiter(limit: int = 1) -> RateLimiter:
    clock = Clock()
    return RateLimiter(limit, 60.0, max_wait=10.0, clock=clock, sleep=clock.sleep)


def test_a_request_without_a_free_slot_fails_without_being_sent() -> None:
    counter = Counter()

    async def twice(client: SsbClient) -> None:
        await client.metadata("05810", "en")
        await client.metadata("05810", "no")

    with pytest.raises(RateLimitedError, match="no request for another 60 seconds") as caught:
        run(twice, counter, limiter=strict_limiter())
    assert caught.value.retry_after == 60
    assert counter.paths == ["metadata"]


def test_cached_responses_take_no_slot(tmp_path: Path) -> None:
    counter = Counter()

    async def twice(client: SsbClient) -> None:
        await client.metadata("05810", "en")
        await client.metadata("05810", "en")

    run(twice, counter, cache=cache_in(tmp_path), limiter=strict_limiter())
    assert counter.paths == ["metadata"]


def test_http_429_holds_back_the_next_request() -> None:
    def limited(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, headers={"Retry-After": "30"})

    counter = Counter(limited)
    limiter = strict_limiter(limit=30)

    async def twice(client: SsbClient) -> list[str]:
        messages: list[str] = []
        for _ in range(2):
            try:
                await client.metadata("05810", "en")
            except RateLimitedError as error:
                messages.append(str(error))
        return messages

    messages = run(twice, counter, limiter=limiter)
    assert messages == [
        "SSB's rate limit was reached. Try again in 30 seconds.",
        "SSB's rate limit allows no request for another 30 seconds. Try again after that.",
    ]
    assert counter.paths == ["metadata"]
