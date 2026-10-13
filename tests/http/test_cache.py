"""The response cache, with responses taken from the recordings."""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from recorded import Json, description, raw

from opencited.http import Exchange
from opencited.http.cache import ResponseCache

RETRIEVED = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 10, 15, 6, 15, tzinfo=UTC)


def recorded_exchange(name: str) -> Exchange:
    request = cast("Json", description(name)["request"])
    response = cast("Json", description(name)["response"])
    body = cast("str | None", request["body"])
    return Exchange(
        method=cast("str", request["method"]),
        url=cast("str", request["url"]),
        body=body.encode() if body is not None else None,
        status=cast("int", response["status"]),
        headers=tuple(cast("dict[str, str]", response["headers"]).items()),
        content=raw(name),
        retrieved_at=RETRIEVED,
    )


class Moment:
    """A clock for the cache that a test can move."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(tmp_path: Path, now: datetime = RETRIEVED) -> tuple[ResponseCache, Moment]:
    moment = Moment(now)
    return ResponseCache(tmp_path / "cache" / "responses.sqlite", now=moment), moment


def get(cache: ResponseCache, exchange: Exchange, body: bytes | None = None) -> Exchange | None:
    sent = exchange.body if body is None else body
    return asyncio.run(cache.get(exchange.method, exchange.url, sent))


def put(cache: ResponseCache, exchange: Exchange, expires_at: datetime = EXPIRES) -> None:
    asyncio.run(cache.put(exchange, expires_at))


@pytest.mark.parametrize("name", ["metadata-05810-en", "data-05810-post-en"])
def test_a_stored_response_comes_back_as_it_arrived(tmp_path: Path, name: str) -> None:
    cache, _ = make(tmp_path)
    exchange = recorded_exchange(name)
    put(cache, exchange)
    assert get(cache, exchange) == exchange


def test_a_response_is_served_until_it_expires(tmp_path: Path) -> None:
    cache, moment = make(tmp_path)
    exchange = recorded_exchange("metadata-05810-en")
    put(cache, exchange)
    moment.now = EXPIRES - timedelta(microseconds=1)
    assert get(cache, exchange) == exchange
    moment.now = EXPIRES
    assert get(cache, exchange) is None


def test_the_key_holds_the_method_the_url_and_the_body(tmp_path: Path) -> None:
    cache, _ = make(tmp_path)
    post = recorded_exchange("data-05810-post-en")
    put(cache, post)
    assert get(cache, post, b'{"selection":[]}') is None
    assert asyncio.run(cache.get("GET", post.url, post.body)) is None
    assert asyncio.run(cache.get(post.method, post.url + "&x=1", post.body)) is None


def test_storing_again_replaces_the_entry(tmp_path: Path) -> None:
    cache, _ = make(tmp_path)
    first = recorded_exchange("metadata-05810-en")
    later = dataclasses.replace(first, retrieved_at=RETRIEVED + timedelta(hours=1))
    put(cache, first)
    put(cache, later)
    assert get(cache, first) == later


def test_storing_drops_expired_entries(tmp_path: Path) -> None:
    cache, moment = make(tmp_path)
    put(cache, recorded_exchange("metadata-05810-en"))
    moment.now = EXPIRES
    put(cache, recorded_exchange("metadata-05810-no"), EXPIRES + timedelta(days=1))
    with sqlite3.connect(cache.path) as connection:
        urls = [row[0] for row in connection.execute("select url from responses")]
    assert urls == [cast("str", cast("Json", description("metadata-05810-no")["request"])["url"])]


def test_only_the_owner_can_read_the_cache(tmp_path: Path) -> None:
    cache, _ = make(tmp_path)
    put(cache, recorded_exchange("metadata-05810-en"))
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache.path.parent.stat().st_mode) == 0o700


def test_a_damaged_cache_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache, _ = make(tmp_path)
    cache.path.parent.mkdir()
    cache.path.write_bytes(b"these bytes are not a database")
    exchange = recorded_exchange("metadata-05810-en")
    put(cache, exchange)
    assert get(cache, exchange) is None
    assert "could not be written" in caplog.text
    assert "could not be read" in caplog.text
