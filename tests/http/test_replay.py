"""Tests for the replay transport and the shared HTTP client. The recordings here are
written by the tests and stand for no source: they only check how recordings are found,
checked and served.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx2
import pytest

from opencited.http import USER_AGENT, make_client, send
from opencited.http.replay import (
    DamagedRecordingError,
    UnrecordedRequestError,
    load_recordings,
    replay_transport,
)

URL = "https://example.org/api/items?lang=en"


def write(
    directory: Path,
    name: str,
    *,
    recorded_at: str = "2026-10-13T20:00:00Z",
    method: str = "GET",
    body: str | None = None,
    answer: bytes = b"{}",
    digest: str | None = None,
) -> None:
    (directory / f"{name}.body").write_bytes(answer)
    document = {
        "name": name,
        "recorded_at": recorded_at,
        "request": {"method": method, "url": URL, "body": body},
        "response": {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body_file": f"{name}.body",
            "body_sha256": digest or hashlib.sha256(answer).hexdigest(),
        },
    }
    (directory / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")


def test_the_newest_recording_wins_whatever_the_file_names(tmp_path: Path) -> None:
    write(tmp_path, "alpha", recorded_at="2026-10-13T20:00:00Z", answer=b'{"day":13}')
    write(tmp_path, "zeta", recorded_at="2026-10-12T20:00:00Z", answer=b'{"day":12}')
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        response = client.get(URL)
    assert response.content == b'{"day":13}'
    assert response.headers["content-type"] == "application/json"


def test_requests_are_told_apart_by_method_and_body(tmp_path: Path) -> None:
    write(tmp_path, "get", answer=b"get")
    write(tmp_path, "post-a", method="POST", body='{"a":1}', answer=b"a")
    write(tmp_path, "post-b", method="POST", body='{"b":2}', answer=b"b")
    assert len(load_recordings(tmp_path)) == 3
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        assert client.get(URL).content == b"get"
        assert client.post(URL, content=b'{"a":1}').content == b"a"
        assert client.post(URL, content=b'{"b":2}').content == b"b"


def test_a_request_without_a_recording_raises(tmp_path: Path) -> None:
    write(tmp_path, "items")
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        with pytest.raises(UnrecordedRequestError, match="no recording"):
            client.get("https://example.org/api/other")
        with pytest.raises(UnrecordedRequestError):
            client.post(URL, content=b"{}")


def test_a_body_that_does_not_match_its_hash_is_refused(tmp_path: Path) -> None:
    write(tmp_path, "items", answer=b'{"changed":true}', digest=hashlib.sha256(b"{}").hexdigest())
    with pytest.raises(DamagedRecordingError, match=r"items\.json"):
        load_recordings(tmp_path)


def test_the_client_names_the_project_follows_no_redirects_and_times_out() -> None:
    client = make_client("https://example.org")
    assert client.headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("opencited/")
    assert client.follow_redirects is False
    assert client.timeout.connect == 10.0
    assert client.timeout.read == 30.0


def test_an_exchange_keeps_the_response_headers(tmp_path: Path) -> None:
    write(tmp_path, "items", answer=b'{"a":1}')

    async def call() -> None:
        async with make_client("https://example.org", transport=replay_transport(tmp_path)) as http:
            exchange = await send(
                http, http.build_request("GET", "/api/items", params={"lang": "en"})
            )
        assert exchange.header("Content-Type") == "application/json"
        assert exchange.header("x-missing") is None
        assert exchange.content == b'{"a":1}'

    asyncio.run(call())
