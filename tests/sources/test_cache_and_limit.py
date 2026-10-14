"""The SSB client with a response cache and a rate limiter, over recorded responses.

Responses written by a test stand only for HTTP 429 and an unreachable source, which
cannot be recorded on demand.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from recorded import FIXTURES, raw

from opencited.http import make_client
from opencited.http.cache import ResponseCache
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.sources import (
    InvalidResponseError,
    RateLimitedError,
    RevisionPeriodError,
    SourceUnavailableError,
)
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
    now: datetime | Callable[[], datetime] = NOON,
    revision_period: bool = True,
) -> T:
    clock = now if callable(now) else lambda: now

    async def call() -> T:
        async with make_client(BASE_URL, transport=counter.transport) as http:
            client = SsbClient(
                http,
                now=clock,
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


def stored_paths(cache: ResponseCache) -> list[str]:
    with sqlite3.connect(cache.path) as connection:
        urls = [row[0] for row in connection.execute("select url from responses")]
    return sorted(httpx2.URL(url).path.rsplit("/", 1)[-1] for url in urls)


def answer_with(name: str, path: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer requests for ``path`` with the body of another recording, and the rest as recorded."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith(path):
            return httpx2.Response(
                200, headers={"content-type": "application/json"}, content=raw(name)
            )
        return REPLAY.handle_request(request)

    return handle


def test_an_answer_that_cannot_be_parsed_is_not_stored(tmp_path: Path) -> None:
    # The recorded search answer, sent for a metadata request.
    counter = Counter(answer_with("search-population-en", "/metadata"))
    cache = cache_in(tmp_path)
    for _ in range(2):
        with pytest.raises(InvalidResponseError):
            run(lambda client: client.metadata("05810", "en"), counter, cache=cache)
    assert counter.paths == ["metadata", "metadata"]


def test_a_reply_with_other_cells_is_not_stored(tmp_path: Path) -> None:
    # The recorded answer to another selection of the same table.
    counter = Counter(answer_with("data-05810-order-en", "/data"))
    cache = cache_in(tmp_path)
    for _ in range(2):
        with pytest.raises(InvalidResponseError, match="other cells"):
            run(lambda client: client.query("05810", POPULATION, "en"), counter, cache=cache)
    assert counter.paths == ["metadata", "data", "data"]
    assert stored_paths(cache) == ["metadata"]


class MovingClock:
    """The limiter's clock, in seconds, and the client's clock, which follows it."""

    def __init__(self, start: datetime) -> None:
        self.start = start
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def moment(self) -> datetime:
        return self.start + timedelta(seconds=self.seconds)

    async def sleep(self, seconds: float) -> None:
        self.seconds += seconds


def test_the_requests_of_a_query_share_one_wait() -> None:
    clock = MovingClock(NOON)
    limiter = RateLimiter(1, 6.0, max_wait=10.0, clock=clock, sleep=clock.sleep)
    asyncio.run(limiter.acquire())
    counter = Counter()
    # The metadata request waits 6 seconds and the data request 6 more.
    with pytest.raises(RateLimitedError):
        run(lambda client: client.query("05810", POPULATION, "en"), counter, limiter=limiter)
    assert counter.paths == ["metadata"]


def test_a_query_that_waits_into_the_revision_period_sends_no_data_request() -> None:
    # 04:59:57 in Oslo; the data request gets its slot 6 seconds later, at 05:00:03.
    clock = MovingClock(datetime(2026, 10, 14, 2, 59, 57, tzinfo=UTC))
    limiter = RateLimiter(1, 6.0, max_wait=10.0, clock=clock, sleep=clock.sleep)
    counter = Counter()
    with pytest.raises(RevisionPeriodError):
        run(
            lambda client: client.query("05810", POPULATION, "en"),
            counter,
            limiter=limiter,
            now=clock.moment,
        )
    assert counter.paths == ["metadata"]


def readings(*moments: datetime) -> Callable[[], datetime]:
    """Return a clock that gives these moments in turn, then the last one again."""
    sequence: Iterator[datetime] = iter(moments)
    last = moments[-1]

    def now() -> datetime:
        nonlocal last
        last = next(sequence, last)
        return last

    return now


@pytest.mark.parametrize(
    ("requested", "received", "stored"),
    [
        # Requested at 08:14:59 in Oslo and received at 08:15:01.
        ("06:14:59", "06:15:01", False),
        # Requested at 04:59:59 and received at 05:00:01.
        ("02:59:59", "03:00:01", False),
        # Requested at 08:15:00, when the period has ended.
        ("06:15:00", "06:15:01", True),
    ],
)
def test_data_requested_or_received_in_the_revision_period_is_not_stored(
    tmp_path: Path, requested: str, received: str, stored: bool
) -> None:
    day = "2026-10-14T{}+00:00"
    times = [datetime.fromisoformat(day.format(time)) for time in (requested, received)]
    cache = cache_in(tmp_path, times[0])
    run(
        lambda client: client.data("05810", POPULATION, "en"),
        Counter(),
        cache=cache,
        now=readings(*times),
    )
    assert stored_paths(cache) == (["data"] if stored else [])


def test_an_entry_lasts_until_0815_through_the_client(tmp_path: Path) -> None:
    counter = Counter()
    run(lambda client: client.metadata("05810", "en"), counter, cache=cache_in(tmp_path))
    # Requested at noon on 14 October; 08:15 in Oslo the next day is 06:15 in UTC.
    expiry = datetime(2026, 10, 15, 6, 15, tzinfo=UTC)
    for moment in (expiry - timedelta(microseconds=1), expiry):
        run(
            lambda client: client.metadata("05810", "en"), counter, cache=cache_in(tmp_path, moment)
        )
    assert counter.paths == ["metadata", "metadata"]
