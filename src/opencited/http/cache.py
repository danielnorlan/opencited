"""A response cache in SQLite, keyed by the method, the URL and a hash of the body (ADR 0008).

An entry keeps the response as it arrived, with its retrieval time, so a result built
from it repeats the original provenance. The caller decides which responses to store
and until when each one is served. The cache holds whole bodies, which can name the
staff behind a table, so its directory and file are readable by their owner only.

A cache that cannot be read or written is skipped with a warning: a request then goes
to the source, as it would without a cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anyio.to_thread

from opencited.http import Exchange

logger = logging.getLogger("opencited.http.cache")

SCHEMA = """
create table if not exists responses (
    method text not null,
    url text not null,
    body_sha256 text not null,
    body blob,
    status integer not null,
    headers text not null,
    content blob not null,
    retrieved_at text not null,
    expires_at text not null,
    primary key (method, url, body_sha256)
)
"""
# How long a call waits for another process that holds the database.
BUSY_SECONDS = 5.0


def body_hash(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


class ResponseCache:
    """Stores responses in one SQLite file. ``now`` lets tests set the clock."""

    def __init__(self, path: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.path = path
        self._now = now
        self._ready = False

    async def get(self, method: str, url: str, body: bytes | None) -> Exchange | None:
        """Return the stored response to this request while it is served, or None."""
        try:
            return await anyio.to_thread.run_sync(self._get, method, url, body)
        except (sqlite3.Error, OSError, ValueError) as error:
            logger.warning("The response cache at %s could not be read: %s", self.path, error)
            return None

    async def put(self, exchange: Exchange, expires_at: datetime) -> None:
        """Store a response, to be served until ``expires_at``, and drop expired ones."""
        try:
            await anyio.to_thread.run_sync(self._put, exchange, expires_at)
        except (sqlite3.Error, OSError) as error:
            logger.warning("The response cache at %s could not be written: %s", self.path, error)

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.touch(mode=0o600, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=BUSY_SECONDS)
        if not self._ready:
            with connection:
                connection.execute("pragma journal_mode=wal")
                connection.execute(SCHEMA)
            self._ready = True
        return connection

    def _get(self, method: str, url: str, body: bytes | None) -> Exchange | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "select status, headers, content, retrieved_at, expires_at from responses"
                " where method = ? and url = ? and body_sha256 = ?",
                (method, url, body_hash(body)),
            ).fetchone()
        if row is None:
            return None
        status, headers, content, retrieved_at, expires_at = cast(
            "tuple[int, str, bytes, str, str]", row
        )
        if datetime.fromisoformat(expires_at) <= self._now():
            return None
        pairs = cast("list[list[str]]", json.loads(headers))
        return Exchange(
            method=method,
            url=url,
            body=body,
            status=status,
            headers=tuple((key, value) for key, value in pairs),
            content=content,
            retrieved_at=datetime.fromisoformat(retrieved_at),
        )

    def _put(self, exchange: Exchange, expires_at: datetime) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "delete from responses where expires_at <= ?", (self._now().isoformat(),)
            )
            connection.execute(
                "insert or replace into responses values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    exchange.method,
                    exchange.url,
                    body_hash(exchange.body),
                    exchange.body,
                    exchange.status,
                    json.dumps(exchange.headers),
                    exchange.content,
                    exchange.retrieved_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
