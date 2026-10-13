"""The HTTP client that source clients share (ADR 0008)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx2

from opencited import __version__

USER_AGENT = f"opencited/{__version__} (+https://github.com/danielnorlan/opencited)"
TIMEOUT = httpx2.Timeout(30.0, connect=10.0)


@dataclass(frozen=True)
class Exchange:
    """One request and the response to it, as the client sent and received them."""

    method: str
    url: str
    body: bytes | None
    status: int
    content_type: str
    content: bytes
    retrieved_at: datetime


def make_client(
    base_url: str, *, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """Return a client with the project's user agent and timeouts that follows no redirects."""
    return httpx2.AsyncClient(
        base_url=base_url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=False,
        transport=transport,
    )


async def send(client: httpx2.AsyncClient, request: httpx2.Request) -> Exchange:
    """Send a request and return the exchange, timed when the request was sent."""
    retrieved_at = datetime.now(UTC)
    response = await client.send(request)
    return Exchange(
        method=request.method,
        url=str(request.url),
        body=request.content or None,
        status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        content=response.content,
        retrieved_at=retrieved_at,
    )
