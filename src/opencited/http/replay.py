"""Replay of recorded responses, for tests (ADR 0008).

A recording is the pair of files that `scripts/record_ssb_fixtures.py` writes: a JSON
file that describes the request and the response, and the stored body. The transport
answers a request with the newest recording of the same method, URL and body. Any other
request raises ``UnrecordedRequestError``, so a test cannot reach the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx2

type Key = tuple[str, str, bytes | None]


class UnrecordedRequestError(LookupError):
    """A request that no recording answers."""


@dataclass(frozen=True)
class Recorded:
    """The response part of one recording."""

    status: int
    headers: dict[str, str]
    body: bytes


def load_recordings(directory: Path) -> dict[Key, Recorded]:
    """Return the newest recording of each request in a directory of fixtures.

    File names end with the date of the recording, so in sorted order a later recording
    of the same request replaces an earlier one.
    """
    recordings: dict[Key, Recorded] = {}
    for path in sorted(directory.glob("*.json")):
        document = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        request = cast("dict[str, object]", document["request"])
        response = cast("dict[str, object]", document["response"])
        body = request["body"]
        key = (
            str(request["method"]),
            str(request["url"]),
            body.encode("utf-8") if isinstance(body, str) else None,
        )
        recordings[key] = Recorded(
            status=cast("int", response["status"]),
            headers=cast("dict[str, str]", response["headers"]),
            body=(directory / str(response["body_file"])).read_bytes(),
        )
    return recordings


def replay_transport(directory: Path) -> httpx2.MockTransport:
    """Return a transport that answers requests from the recordings in ``directory``."""
    recordings = load_recordings(directory)

    def handle(request: httpx2.Request) -> httpx2.Response:
        recorded = recordings.get((request.method, str(request.url), request.content or None))
        if recorded is None:
            raise UnrecordedRequestError(f"no recording for {request.method} {request.url}")
        return httpx2.Response(recorded.status, headers=recorded.headers, content=recorded.body)

    return httpx2.MockTransport(handle)
