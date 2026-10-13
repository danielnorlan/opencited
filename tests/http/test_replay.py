"""Tests for the replay transport. The recordings here are written by the tests and stand
for no source: they only check how recordings are found and served.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from opencited.http.replay import UnrecordedRequestError, load_recordings, replay_transport

URL = "https://example.org/api/items?lang=en"


def write(directory: Path, name: str, *, method: str, body: str | None, answer: bytes) -> None:
    (directory / f"{name}.body").write_bytes(answer)
    document = {
        "name": name.split(".")[0],
        "request": {"method": method, "url": URL, "body": body},
        "response": {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body_file": f"{name}.body",
        },
    }
    (directory / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")


def test_the_newest_recording_of_a_request_wins(tmp_path: Path) -> None:
    write(tmp_path, "items.2026-10-12", method="GET", body=None, answer=b'{"day":12}')
    write(tmp_path, "items.2026-10-13", method="GET", body=None, answer=b'{"day":13}')
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        response = client.get(URL)
    assert response.content == b'{"day":13}'
    assert response.headers["content-type"] == "application/json"


def test_requests_are_told_apart_by_method_and_body(tmp_path: Path) -> None:
    write(tmp_path, "get.2026-10-13", method="GET", body=None, answer=b"get")
    write(tmp_path, "post-a.2026-10-13", method="POST", body='{"a":1}', answer=b"a")
    write(tmp_path, "post-b.2026-10-13", method="POST", body='{"b":2}', answer=b"b")
    assert len(load_recordings(tmp_path)) == 3
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        assert client.get(URL).content == b"get"
        assert client.post(URL, content=b'{"a":1}').content == b"a"
        assert client.post(URL, content=b'{"b":2}').content == b"b"


def test_a_request_without_a_recording_raises(tmp_path: Path) -> None:
    write(tmp_path, "items.2026-10-13", method="GET", body=None, answer=b"{}")
    with httpx2.Client(transport=replay_transport(tmp_path)) as client:
        with pytest.raises(UnrecordedRequestError, match="no recording"):
            client.get("https://example.org/api/other")
        with pytest.raises(UnrecordedRequestError):
            client.post(URL, content=b"{}")
