"""The MCP server's tools, called through an MCP client connected in the same process.

The server replays the recordings in `tests/fixtures/ssb/`, and the expected results
come from the same client called directly, or from the recordings themselves.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx2
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel
from recorded import FIXTURES, Json, description, recorded_cells

from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.sources import Language, QueryResult, SearchResult, TableMetadata
from opencited.sources.ssb import BASE_URL, SsbClient

REPLAY = replay_transport(FIXTURES)
TOOLS = ("search_tables", "get_table_metadata", "query_table")
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}
SYMBOLS = {
    "RegionSamisk": ["1849", "1850", "1853"],
    "ContentsCode": ["*"],
    "Tid": ["2008", "2009", "2010"],
}


class Recorder:
    """A transport that replays the recordings and keeps the paths it was asked for."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        return REPLAY.handle_request(request)


def session[T](
    work: Callable[[Client], Awaitable[T]], transport: httpx2.AsyncBaseTransport = REPLAY
) -> T:
    async def run() -> T:
        async with Client(build_server(transport=transport, revision_period=False)) as client:
            return await work(client)

    return asyncio.run(run())


def listed() -> list[Tool]:
    async def work(client: Client) -> list[Tool]:
        return (await client.list_tools()).tools

    return session(work)


def call(
    name: str, arguments: dict[str, Any], transport: httpx2.AsyncBaseTransport = REPLAY
) -> CallToolResult:
    async def work(client: Client) -> CallToolResult:
        return await client.call_tool(name, arguments)

    return session(work, transport)


def direct[T](work: Callable[[SsbClient], Awaitable[T]]) -> T:
    async def run() -> T:
        async with make_client(BASE_URL, transport=REPLAY) as http:
            return await work(SsbClient(http, revision_period=False))

    return asyncio.run(run())


def structured(result: CallToolResult) -> Json:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return cast("Json", result.structured_content)


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    assert result.structured_content is None
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text


def test_the_tools_come_in_a_fixed_order() -> None:
    assert [tool.name for tool in listed()] == list(TOOLS)


def test_every_tool_describes_itself_and_its_arguments() -> None:
    for tool in listed():
        assert tool.description
        assert tool.input_schema["type"] == "object"
        properties = cast("dict[str, Json]", tool.input_schema["properties"])
        assert properties
        assert all(schema.get("description") for schema in properties.values()), tool.name
        assert "ctx" not in properties


def test_every_tool_only_reads() -> None:
    for tool in listed():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True


def test_the_output_schemas_are_the_schemas_of_the_results() -> None:
    models: tuple[type[BaseModel], ...] = (SearchResult, TableMetadata, QueryResult)
    for tool, model in zip(listed(), models, strict=True):
        assert tool.output_schema == model.model_json_schema(mode="serialization")


def numbers(schema: object) -> list[object]:
    """Return every part of a JSON schema that admits a JSON number other than an integer."""
    if isinstance(schema, dict):
        node = cast("dict[str, object]", schema)
        found: list[object] = [node] if node.get("type") == "number" else []
        return found + [part for value in node.values() for part in numbers(value)]
    if isinstance(schema, list):
        return [part for value in cast("list[object]", schema) for part in numbers(value)]
    return []


def test_values_are_decimal_strings_in_the_output_schema() -> None:
    schema = cast("Json", listed()[2].output_schema)
    assert numbers(schema) == []
    observation = cast("dict[str, Json]", schema["$defs"])["Observation"]
    value = cast("dict[str, Json]", observation["properties"])["value"]
    branches = cast("list[Json]", value["anyOf"])
    assert {"type": "null"} in branches
    assert any(branch.get("type") == "string" and "pattern" in branch for branch in branches)


@pytest.mark.parametrize(("query", "language"), [("befolkning", "no"), ("population", "en")])
def test_search_returns_what_the_client_returns(query: str, language: Language) -> None:
    result = call("search_tables", {"query": query, "language": language})
    expected = direct(lambda client: client.search(query, language))
    assert structured(result) == expected.model_dump(mode="json")


@pytest.mark.parametrize(
    ("query", "language", "page"),
    [
        ("befolkning", "no", "https://www.ssb.no/statbank/table/"),
        ("population", "en", "https://www.ssb.no/en/statbank/table/"),
    ],
)
def test_search_and_metadata_link_to_the_table_in_statbank(
    query: str, language: str, page: str
) -> None:
    search = structured(call("search_tables", {"query": query, "language": language}))
    tables = cast("list[Json]", search["tables"])
    assert tables
    assert all(table["url"] == f"{page}{table['table_id']}" for table in tables)
    metadata = structured(call("get_table_metadata", {"table_id": "05810", "language": language}))
    assert metadata["url"] == f"{page}05810"


@pytest.mark.parametrize("table_id", ["05810", "06743", "06856"])
def test_metadata_returns_what_the_client_returns(table_id: str) -> None:
    result = call("get_table_metadata", {"table_id": table_id, "language": "en"})
    expected = direct(lambda client: client.metadata(table_id, "en"))
    assert structured(result) == expected.model_dump(mode="json")


@pytest.mark.parametrize(
    ("table_id", "selection", "name"),
    [("05810", POPULATION, "data-05810-en"), ("06856", SYMBOLS, "data-06856-symbols-en")],
)
def test_queries_return_the_values_as_ssb_wrote_them(
    table_id: str, selection: dict[str, list[str]], name: str
) -> None:
    content = structured(
        call("query_table", {"table_id": table_id, "selection": selection, "language": "en"})
    )
    result = QueryResult.model_validate_json(json.dumps(content))
    observations = cast("list[Json]", content["observations"])
    expected = recorded_cells(name)
    assert [tuple(cast("list[str]", cell["codes"])) for cell in observations] == [
        codes for codes, _, _ in expected
    ]
    assert [cell["value"] for cell in observations] == [
        None if symbol is not None else token for _, token, symbol in expected
    ]
    assert [cell["symbol"] for cell in observations] == [symbol for _, _, symbol in expected]
    response = cast("Json", description(name)["response"])
    request = cast("Json", description(name)["request"])
    assert result.provenance.response_sha256 == response["body_sha256"]
    assert result.provenance.request.url == request["url"]


def test_symbols_in_a_query_come_with_their_explanations() -> None:
    content = structured(
        call("query_table", {"table_id": "06856", "selection": SYMBOLS, "language": "en"})
    )
    symbols = {
        cast("str", cell["symbol"])
        for cell in cast("list[Json]", content["observations"])
        if cell["symbol"] is not None
    }
    explained = cast("list[Json]", content["symbols"])
    assert symbols
    assert {entry["symbol"] for entry in explained} == symbols
    assert all(entry["explanation"] for entry in explained)


def test_errors_from_ssb_reach_the_client_with_the_reason() -> None:
    text = error_text(call("get_table_metadata", {"table_id": "99999", "language": "en"}))
    assert "SSB has no table 99999" in text


def test_invalid_selections_reach_the_client_before_a_data_request() -> None:
    recorder = Recorder()
    selection = POPULATION | {"Kjonn": ["9"]}
    result = call(
        "query_table",
        {"table_id": "05810", "selection": selection, "language": "en"},
        recorder.transport,
    )
    text = error_text(result)
    assert "Kjonn has no code '9'" in text
    assert "The codes of Kjonn are" in text
    assert not any(path.endswith("/data") for path in recorder.paths)


@pytest.mark.parametrize(
    ("name", "arguments", "reason"),
    [
        ("get_table_metadata", {"table_id": "5810"}, "table_id"),
        ("get_table_metadata", {"table_id": "05810", "language": "de"}, "language"),
        ("query_table", {"table_id": "05810"}, "selection"),
        ("query_table", {"table_id": "05810", "selection": {"Tid": "2026"}}, "selection"),
        ("search_tables", {"query": "population", "limit": 0}, "limit"),
        ("search_tables", {"query": "population", "limit": 51}, "limit"),
        ("search_tables", {"query": "  "}, "query"),
    ],
)
def test_invalid_arguments_fail_without_a_request(
    name: str, arguments: dict[str, Any], reason: str
) -> None:
    recorder = Recorder()
    text = error_text(call(name, arguments, recorder.transport))
    assert reason in text
    # The only request is the one for SSB's configuration, sent when the server starts.
    assert recorder.paths == ["/api/pxwebapi/v2/config"]
