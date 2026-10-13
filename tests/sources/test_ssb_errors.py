"""How the SSB client maps statuses and transport failures that no recording shows.

SSB's own error answers, for an unknown table and for rejected selections, are covered
by the contract tests, which replay recordings. The responses here are written by the
tests and stand for no source: rate limits, server errors, redirects, broken bodies and
failed connections, which cannot be recorded on demand.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx2
import pytest

from opencited.http import make_client
from opencited.sources import (
    InvalidResponseError,
    RateLimitedError,
    SourceError,
    SourceUnavailableError,
)
from opencited.sources.ssb import BASE_URL, SsbClient

JSON = {"content-type": "application/json"}
HTML = {"content-type": "text/html"}


def config_with(handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
    async def call() -> None:
        async with make_client(BASE_URL, transport=httpx2.MockTransport(handler)) as http:
            await SsbClient(http).config()

    asyncio.run(call())


def answer(
    status: int, headers: dict[str, str], body: bytes
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers=headers, content=body)

    return handler


@pytest.mark.parametrize(
    ("status", "headers", "body", "error", "message"),
    [
        (429, JSON, b"{}", RateLimitedError, "rate limit"),
        (429, HTML, b"<html/>", RateLimitedError, "rate limit"),
        (429, JSON, b"", RateLimitedError, "rate limit"),
        (500, JSON, b"{}", SourceUnavailableError, "HTTP 500"),
        (503, JSON, b"", SourceUnavailableError, "HTTP 503"),
        (502, HTML, b"Bad Gateway", SourceUnavailableError, "HTTP 502"),
        (301, JSON, b"{}", SourceUnavailableError, "HTTP 301"),
        (101, JSON, b"{}", SourceUnavailableError, "HTTP 101"),
        (200, HTML, b"<html/>", SourceUnavailableError, "no JSON body"),
        (200, JSON, b"{", InvalidResponseError, "not valid JSON"),
        (200, JSON, b"[1E+999999999999999999999]", InvalidResponseError, "not valid JSON"),
        (200, JSON, b"[]", InvalidResponseError, "must be an object"),
    ],
)
def test_statuses_and_bodies_map_to_errors(
    status: int, headers: dict[str, str], body: bytes, error: type[SourceError], message: str
) -> None:
    with pytest.raises(error, match=message):
        config_with(answer(status, headers, body))


@pytest.mark.parametrize(
    ("header", "seconds"), [("30", 30), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("1.5", None)]
)
def test_a_rate_limit_carries_the_wait_that_the_source_asks_for(
    header: str, seconds: int | None
) -> None:
    with pytest.raises(RateLimitedError) as caught:
        config_with(answer(429, {**JSON, "Retry-After": header}, b"{}"))
    assert caught.value.retry_after == seconds


def test_a_body_that_cannot_be_decoded_is_invalid() -> None:
    broken = b"these bytes are not gzip"
    with pytest.raises(InvalidResponseError, match="could not be decoded"):
        config_with(answer(200, {**JSON, "Content-Encoding": "gzip"}, broken))


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx2.ReadTimeout("slow"), "did not answer in time"),
        (httpx2.ConnectError("down"), "could not be reached"),
    ],
)
def test_transport_failures_are_unavailable(failure: Exception, message: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise failure

    with pytest.raises(SourceUnavailableError, match=message):
        config_with(handler)
