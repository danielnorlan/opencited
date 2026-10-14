"""The live smoke test: every tool through `opencited mcp` over stdio, against SSB's API.

The tests are marked `live`, so the default run makes no network calls, and the nightly
workflow runs them with `pytest -m live`. They check the form of each result, since
SSB's figures change with each publication, and compare the table's dimensions with
the recording, so that a change in the API shows up before the fixtures are recorded
again. A run should avoid 04:55 to 05:00 and 11:25 to 11:45 Norwegian time, when SSB
updates its metadata (ADR 0007, decision 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, TextContent
from recorded import FIXTURES

from opencited.http import USER_AGENT, make_client
from opencited.http.replay import replay_transport
from opencited.sources import QueryResult, SearchResult, TableMetadata
from opencited.sources.ssb import BASE_URL, SsbClient, in_revision_period

pytestmark = pytest.mark.live

COMMAND = [sys.executable, "-m", "opencited", "mcp", "--no-cache"]
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["top(1)"],
}
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_tables", {"query": "population", "language": "en"}),
    ("get_table_metadata", {"table_id": "05810", "language": "en"}),
    ("query_table", {"table_id": "05810", "selection": POPULATION, "language": "en"}),
]


@dataclass
class Session:
    names: list[str]
    search: CallToolResult
    metadata: CallToolResult
    query: CallToolResult
    started: datetime
    finished: datetime


@pytest.fixture(scope="module")
def session() -> Session:
    """Start the server once, list its tools and call each of them."""

    async def run() -> Session:
        async with Client(StdioServerParameters(command=COMMAND[0], args=COMMAND[1:])) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            started = datetime.now(UTC)
            search, metadata, query = [
                await client.call_tool(name, arguments) for name, arguments in CALLS
            ]
            return Session(names, search, metadata, query, started, datetime.now(UTC))

    return asyncio.run(run())


def content(result: CallToolResult) -> str:
    assert not result.is_error, result.content
    return json.dumps(result.structured_content)


def test_the_server_lists_the_three_tools(session: Session) -> None:
    assert session.names == ["search_tables", "get_table_metadata", "query_table"]


def test_search_finds_tables_with_links(session: Session) -> None:
    result = SearchResult.model_validate_json(content(session.search))
    assert 0 < len(result.tables) <= 10
    assert result.total >= len(result.tables)
    assert all(table.title for table in result.tables)
    assert any("population" in table.title.lower() for table in result.tables)
    for table in result.tables:
        assert table.url == f"https://www.ssb.no/en/statbank/table/{table.table_id}"


def recorded_metadata() -> TableMetadata:
    """Return the recorded metadata of table 05810, parsed as the client parses it."""

    async def read() -> TableMetadata:
        transport = replay_transport(FIXTURES)
        async with make_client(BASE_URL, transport=transport) as http:
            return await SsbClient(http, revision_period=False).metadata("05810", "en")

    return asyncio.run(read())


def test_the_table_keeps_the_recorded_dimensions_and_codes(session: Session) -> None:
    result = TableMetadata.model_validate_json(content(session.metadata))
    assert result.table_id == "05810"
    assert result.url == "https://www.ssb.no/en/statbank/table/05810"
    recorded = recorded_metadata()
    flags = [(d.code, d.time, d.contents, d.eliminable) for d in result.dimensions]
    assert flags == [(d.code, d.time, d.contents, d.eliminable) for d in recorded.dimensions]
    # SSB adds periods over time, so the recorded codes must still be there.
    for live, then in zip(result.dimensions, recorded.dimensions, strict=True):
        assert {value.code for value in then.values} <= {value.code for value in live.values}


def revision_error(result: CallToolResult) -> bool:
    """Return whether a result is the error that names SSB's revision period."""
    if not result.is_error:
        return False
    [text] = result.content
    return isinstance(text, TextContent) and "SSB's revision period" in text.text


def test_a_query_gives_the_latest_value_with_provenance(session: Session) -> None:
    revised = {in_revision_period(session.started), in_revision_period(session.finished)}
    if revised == {True}:
        # From 05:00 to 08:15 Norwegian time the server fetches no data, by design.
        assert revision_error(session.query), session.query.content
        return
    if revised == {True, False} and revision_error(session.query):
        # The call crossed 05:00 or 08:15, so the revision period's error is right too.
        return
    result = QueryResult.model_validate_json(content(session.query))
    assert result.cached is False
    metadata = TableMetadata.model_validate_json(content(session.metadata))
    [time] = [dimension for dimension in metadata.dimensions if dimension.time]
    [cell] = result.observations
    assert cell.codes == ("0", "999B", "Personer", time.values[-1].code)
    # The population of Norway is a positive whole number of persons.
    assert cell.value is not None
    assert cell.value > 0
    assert cell.value == cell.value.to_integral_value()
    record = result.provenance
    assert (record.source_id, record.license, record.table_id) == ("ssb", "CC-BY-4.0", "05810")
    assert record.table_url == "https://www.ssb.no/en/statbank/table/05810"
    assert record.request.url.startswith(f"{BASE_URL}/tables/05810/data?")
    assert session.started <= record.retrieved_at <= session.finished
    # SSB sends the same bytes for the same request until it publishes again.
    again = httpx2.get(record.request.url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
    assert again.status_code == 200
    assert record.response_sha256 == hashlib.sha256(again.content).hexdigest()
