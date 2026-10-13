"""The `opencited mcp` command over stdio and Streamable HTTP, as clients start it.

Each test starts the command in a subprocess that replays the recordings in
`tests/fixtures/ssb/` with `--replay`.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from mcp import Client, StdioServerParameters
from mcp.client import IncomingMessage
from mcp.types import CallToolResult
from recorded import FIXTURES, Json, description, get, recorded

COMMAND = [sys.executable, "-m", "opencited", "mcp", "--replay", str(FIXTURES)]
TOOLS = ["search_tables", "get_table_metadata", "query_table"]
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_tables", {"query": "population", "language": "en"}),
    ("get_table_metadata", {"table_id": "05810", "language": "en"}),
    (
        "query_table",
        {
            "table_id": "05810",
            "selection": {
                "Kjonn": ["0"],
                "Alder": ["999B"],
                "ContentsCode": ["Personer"],
                "Tid": ["2025", "2026"],
            },
            "language": "en",
        },
    ),
]
# How long a server may take to start before a test fails.
STARTUP_SECONDS = 20
ACCEPT = {"Accept": "application/json, text/event-stream"}
# A first request in each protocol era: the 2025-06-18 handshake, which opens a
# session, and a 2026-07-28 request, which carries its version in a header and in
# the body.
LEGACY = (
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    },
    ACCEPT,
)
MODERN = (
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    },
    ACCEPT | {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"},
)
ERAS = pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["2025-06-18", "2026-07-28"])
type Era = tuple[dict[str, Any], dict[str, str]]


@dataclass
class Session:
    """The listed tools, the results of the calls in CALLS, and the transport's faults."""

    names: list[str]
    results: list[CallToolResult]
    faults: list[Exception]


def session(server: StdioServerParameters | str) -> Session:
    faults: list[Exception] = []

    async def collect(message: IncomingMessage) -> None:
        # The client skips output that is not a protocol message, such as a log line
        # on stdout, and reports it here.
        if isinstance(message, Exception):
            faults.append(message)

    async def run() -> Session:
        async with Client(server, message_handler=collect) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            results = [await client.call_tool(name, arguments) for name, arguments in CALLS]
            return Session(names, results, faults)

    return asyncio.run(run())


def check(seen: Session) -> None:
    assert seen.faults == []
    assert seen.names == TOOLS
    results = seen.results
    for result in results:
        assert not result.is_error, result.content
        assert result.structured_content is not None
    search, metadata, query = (cast("Json", result.structured_content) for result in results)
    body = recorded("search-population-en")
    assert search["total"] == get(body, "page", "totalElements")
    assert [table["table_id"] for table in cast("list[Json]", search["tables"])] == [
        table["id"] for table in cast("list[Json]", body["tables"])
    ]
    assert metadata["table_id"] == "05810"
    label = cast("str", get(recorded("metadata-05810-en"), "label"))
    assert metadata["title"] == label.removeprefix("05810: ")
    provenance = cast("Json", query["provenance"])
    response = cast("Json", description("data-05810-en")["response"])
    assert provenance["response_sha256"] == response["body_sha256"]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return cast("int", probe.getsockname()[1])


@dataclass
class HttpServer:
    address: str
    log: Path


def wait_for(port: int, process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"the server stopped with exit code {process.returncode}:\n{log.read_text()}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"the server did not listen on port {port} in {STARTUP_SECONDS} s")


@pytest.fixture(scope="module")
def http_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[HttpServer]:
    """Start `opencited mcp --http` on a free port, with its log in a file."""
    port = free_port()
    log = tmp_path_factory.mktemp("server") / "stderr.log"
    with log.open("wb") as errors:
        process = subprocess.Popen(
            [*COMMAND, "--http", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=errors
        )
    try:
        wait_for(port, process, log)
        yield HttpServer(f"http://127.0.0.1:{port}/mcp", log)
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_the_tools_answer_over_stdio() -> None:
    check(session(StdioServerParameters(command=COMMAND[0], args=COMMAND[1:])))


def test_the_tools_answer_over_http(http_server: HttpServer) -> None:
    check(session(http_server.address))


def test_the_server_warns_that_it_replays_recordings(http_server: HttpServer) -> None:
    log = http_server.log.read_text()
    assert f"Serving the responses recorded in {FIXTURES} instead of calling SSB" in log


def post(server: HttpServer, era: Era, headers: dict[str, str]) -> httpx2.Response:
    body, protocol = era
    return httpx2.post(server.address, json=body, headers=protocol | headers, timeout=10)


@ERAS
def test_http_accepts_a_local_origin(http_server: HttpServer, era: Era) -> None:
    origin = http_server.address.removesuffix("/mcp")
    assert post(http_server, era, {"Origin": origin}).status_code == 200


@ERAS
def test_http_refuses_a_foreign_origin(http_server: HttpServer, era: Era) -> None:
    response = post(http_server, era, {"Origin": "https://example.com"})
    assert response.status_code == 403


@ERAS
def test_http_refuses_a_foreign_host(http_server: HttpServer, era: Era) -> None:
    response = post(http_server, era, {"Host": "example.com"})
    assert response.status_code == 421
