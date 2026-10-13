"""The `opencited mcp` command over stdio and Streamable HTTP, as clients start it.

Each test starts the command in a subprocess that replays the recordings in
`tests/fixtures/ssb/`, through the variable `OPENCITED_SSB_FIXTURES`.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any, cast

import httpx2
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult
from recorded import FIXTURES, Json, description, get, recorded

from opencited import __version__
from opencited.cli import FIXTURES as FIXTURES_VARIABLE
from opencited.cli import parse_arguments

COMMAND = [sys.executable, "-m", "opencited", "mcp"]
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
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def session(server: StdioServerParameters | str) -> tuple[list[str], list[CallToolResult]]:
    """Return the names of the listed tools and the results of the calls in CALLS."""

    async def run() -> tuple[list[str], list[CallToolResult]]:
        async with Client(server) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            results = [await client.call_tool(name, arguments) for name, arguments in CALLS]
            return names, results

    return asyncio.run(run())


def check(names: list[str], results: list[CallToolResult]) -> None:
    assert names == TOOLS
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


def wait_for(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the server stopped with exit code {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"the server did not listen on port {port} in {STARTUP_SECONDS} s")


@pytest.fixture(scope="module")
def http_server() -> Iterator[str]:
    """Start `opencited mcp --http` on a free port and return its address."""
    port = free_port()
    environment = os.environ | {FIXTURES_VARIABLE: str(FIXTURES)}
    process = subprocess.Popen(
        [*COMMAND, "--http", "--port", str(port)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(port, process)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_the_tools_answer_over_stdio() -> None:
    server = StdioServerParameters(
        command=COMMAND[0], args=COMMAND[1:], env={FIXTURES_VARIABLE: str(FIXTURES)}
    )
    check(*session(server))


def test_the_tools_answer_over_http(http_server: str) -> None:
    check(*session(http_server))


def post(address: str, headers: dict[str, str]) -> httpx2.Response:
    return httpx2.post(address, json=INITIALIZE, headers=MCP_HEADERS | headers, timeout=10)


def test_http_accepts_a_local_origin(http_server: str) -> None:
    origin = http_server.removesuffix("/mcp")
    assert post(http_server, {"Origin": origin}).status_code == 200


def test_http_refuses_a_foreign_origin(http_server: str) -> None:
    response = post(http_server, {"Origin": "https://example.com"})
    assert response.status_code == 403


def test_http_refuses_a_foreign_host(http_server: str) -> None:
    response = post(http_server, {"Host": "example.com"})
    assert response.status_code == 421


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])  # noqa: S104 - the test checks that this address is refused
def test_the_command_serves_loopback_addresses_only(host: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        parse_arguments(["mcp", "--http", "--host", host])
    assert stopped.value.code == 2


def test_the_command_reports_its_version() -> None:
    finished = subprocess.run(
        [sys.executable, "-m", "opencited", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert finished.stdout.strip() == f"opencited {__version__}"
