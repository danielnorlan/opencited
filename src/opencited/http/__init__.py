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
    """One request and the response to it, as the client sent and received them.

    ``headers`` holds the response headers with lower-case names, in the order received.
    """

    method: str
    url: str
    body: bytes | None
    status: int
    headers: tuple[tuple[str, str], ...]
    content: bytes
    retrieved_at: datetime

    def header(self, name: str) -> str | None:
        """Return the first response header with this name, or None."""
        wanted = name.lower()
        return next((value for key, value in self.headers if key == wanted), None)

    @property
    def content_type(self) -> str:
        """Return the content type of the response, or an empty string."""
        return self.header("content-type") or ""


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
        headers=tuple((key.lower(), value) for key, value in response.headers.items()),
        content=response.content,
        retrieved_at=retrieved_at,
    )
