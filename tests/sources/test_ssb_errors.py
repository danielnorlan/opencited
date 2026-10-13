"""How the SSB client maps HTTP statuses and transport failures to its errors.

The responses here are written by the tests and are not SSB's content. Responses that
SSB sent are covered by the contract tests, which replay recordings.
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
    RejectedRequestError,
    SourceError,
    SourceUnavailableError,
)
from opencited.sources.ssb import BASE_URL, SsbClient

JSON = {"content-type": "application/json"}


def config_with(handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
    async def call() -> None:
        async with make_client(BASE_URL, transport=httpx2.MockTransport(handler)) as http:
            await SsbClient(http).config()

    asyncio.run(call())


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (429, b"{}", RateLimitedError, "rate limit"),
        (500, b"{}", SourceUnavailableError, "HTTP 500"),
        (503, b"{}", SourceUnavailableError, "HTTP 503"),
        (400, b'{"title":"Invented title"}', RejectedRequestError, "Invented title"),
        (400, b"[]", RejectedRequestError, "no reason given"),
        (200, b"{", InvalidResponseError, "not valid JSON"),
        (200, b"[]", InvalidResponseError, "not an object"),
    ],
)
def test_statuses_and_bodies_map_to_errors(
    status: int, body: bytes, error: type[SourceError], message: str
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers=JSON, content=body)

    with pytest.raises(error, match=message):
        config_with(handler)


def test_an_answer_without_json_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/html"}, content=b"<html/>")

    with pytest.raises(SourceUnavailableError, match="no JSON body"):
        config_with(handler)


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
