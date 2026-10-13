"""Replay of recorded responses, for tests (ADR 0008).

A recording is the pair of files that `scripts/record_ssb_fixtures.py` writes: a JSON
file that describes the request and the response, and the stored body. The transport
answers a request with the newest recording of the same method, URL and body, after it
has checked the body against the SHA-256 in the description. Any other request raises
``UnrecordedRequestError``, so a test cannot reach the network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx2

type Key = tuple[str, str, bytes | None]


class UnrecordedRequestError(LookupError):
    """A request that no recording answers."""


class DamagedRecordingError(ValueError):
    """A recording whose stored body does not match the hash in its description."""


@dataclass(frozen=True)
class Recorded:
    """The response part of one recording."""

    recorded_at: str
    status: int
    headers: dict[str, str]
    body: bytes
    body_sha256: str


def read_recording(path: Path) -> tuple[Key, Recorded]:
    """Return the request key and the response of one recording, after checking its hash."""
    document = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    request = cast("dict[str, object]", document["request"])
    response = cast("dict[str, object]", document["response"])
    body = (path.parent / str(response["body_file"])).read_bytes()
    digest = str(response["body_sha256"])
    if hashlib.sha256(body).hexdigest() != digest:
        raise DamagedRecordingError(f"{path.name}: the stored body does not match its hash")
    sent = request["body"]
    key = (
        str(request["method"]),
        str(request["url"]),
        sent.encode("utf-8") if isinstance(sent, str) else None,
    )
    recorded = Recorded(
        recorded_at=str(document["recorded_at"]),
        status=cast("int", response["status"]),
        headers=cast("dict[str, str]", response["headers"]),
        body=body,
        body_sha256=digest,
    )
    return key, recorded


def load_recordings(directory: Path) -> dict[Key, Recorded]:
    """Return the newest recording of each request in a directory of fixtures.

    Recordings are compared by the time they were recorded, which the description holds
    as a UTC timestamp in ISO 8601, so the text sorts in time order.
    """
    recordings: dict[Key, Recorded] = {}
    for path in directory.glob("*.json"):
        key, recorded = read_recording(path)
        earlier = recordings.get(key)
        if earlier is None or recorded.recorded_at > earlier.recorded_at:
            recordings[key] = recorded
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
