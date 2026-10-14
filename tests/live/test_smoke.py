"""The live smoke test: every tool through `opencited mcp` over stdio, against SSB's API.

The tests are marked `live`, so the default run makes no network calls, and the nightly
workflow runs them with `pytest -m live`. They check the form of each result, since
SSB's figures change with each publication, and compare the table's dimensions with
the recording, so that a change in the API shows up before the fixtures are recorded
again.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, TextContent
from recorded import recorded

from opencited.sources import QueryResult, SearchResult, TableMetadata
from opencited.sources.ssb import BASE_URL, in_revision_period
from opencited.values import DECIMAL_PATTERN

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
    assert result.total > 0
    assert result.tables
    for table in result.tables:
        assert table.url == f"https://www.ssb.no/en/statbank/table/{table.table_id}"


def test_the_table_has_the_recorded_dimensions(session: Session) -> None:
    result = TableMetadata.model_validate_json(content(session.metadata))
    assert result.table_id == "05810"
    assert result.url == "https://www.ssb.no/en/statbank/table/05810"
    recorded_codes = cast("list[str]", recorded("metadata-05810-en")["id"])
    assert [dimension.code for dimension in result.dimensions] == recorded_codes


def test_a_query_gives_the_latest_value_with_provenance(session: Session) -> None:
    revised = {in_revision_period(session.started), in_revision_period(session.finished)}
    if revised == {True}:
        # From 05:00 to 08:15 Norwegian time the server fetches no data, by design.
        [text] = session.query.content
        assert isinstance(text, TextContent)
        assert "revision period" in text.text
        return
    if revised == {True, False} and session.query.is_error:
        # The call crossed 05:00 or 08:15, so the revision period's error is right too.
        return
    result = QueryResult.model_validate_json(content(session.query))
    assert result.cached is False
    [cell] = result.observations
    assert cell.codes[:3] == ("0", "999B", "Personer")
    if cell.value is None:
        assert cell.symbol is not None
    else:
        assert re.fullmatch(DECIMAL_PATTERN, format(cell.value, "f"))
    record = result.provenance
    assert (record.source_id, record.license, record.table_id) == ("ssb", "CC-BY-4.0", "05810")
    assert record.table_url == "https://www.ssb.no/en/statbank/table/05810"
    assert record.request.url.startswith(f"{BASE_URL}/tables/05810/data?")
    assert session.started <= record.retrieved_at <= session.finished
    assert re.fullmatch("[0-9a-f]{64}", record.response_sha256)
