"""The agent's toolbox against the MCP server in the same process (spec 0004).

The server replays the recordings in `tests/fixtures/ssb/` and keeps snapshots in a
temporary store. Expected results come from the source client and the engine called
directly, or from the recordings.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from mcp import Client
from mcp.types import CallToolResult, Tool
from recorded import FIXTURES
from snapshotting import CATCH, POPULATION, SYMBOLS, population, retrieve

from opencited.agent import tools
from opencited.agent.model import ToolCall, ToolSchema, tool_param
from opencited.agent.tools import (
    CALCULATE,
    EVIDENCE_ID,
    SERVER_TOOLS,
    Toolbox,
    ToolOutcome,
    calculate_schema,
    calculation_view,
    formatted,
    source_view,
)
from opencited.calc.engine import Engine
from opencited.calc.plan import Plan, plan_schema
from opencited.calc.result import CalculationResult
from opencited.gate import CalculationEvidence, Evidence, SourceEvidence, check
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.snapshots import SnapshotStore
from opencited.sources import Language, QueryResult, SearchResult, TableMetadata, Unit
from opencited.sources.ssb import BASE_URL, SsbClient
from opencited.values import NO_BREAK_SPACE, exact_text, format_number

REPLAY = replay_transport(FIXTURES)
CONFIG = "/api/pxwebapi/v2/config"
SHA256 = re.compile(r"[0-9a-f]{64}")

type Step = ToolCall | Callable[[SnapshotStore], None]


class Recorder:
    """A transport that replays the recordings and keeps the paths of data requests."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        # The server reads SSB's configuration in the background when it starts.
        if request.url.path != CONFIG:
            self.paths.append(request.url.path)
        return REPLAY.handle_request(request)


@dataclass
class Run:
    outcomes: list[ToolOutcome]
    evidence: tuple[Evidence, ...]
    schemas: tuple[ToolSchema, ...]
    paths: list[str]
    store: SnapshotStore


def run(tmp_path: Path, *steps: Step, language: Language = "no", keep: bool = True) -> Run:
    """Run calls through a toolbox; a function among them works on the snapshot store."""
    store = SnapshotStore(tmp_path / "snapshots")
    recorder = Recorder()

    async def work() -> Run:
        server = build_server(
            transport=recorder.transport,
            revision_period=False,
            snapshots=store if keep else None,
        )
        async with Client(server) as client:
            toolbox = await Toolbox.connect(client, store, language)
            outcomes: list[ToolOutcome] = []
            for step in steps:
                if isinstance(step, ToolCall):
                    outcomes.append(await toolbox.run(step))
                else:
                    step(store)
            return Run(outcomes, toolbox.evidence, toolbox.schemas, recorder.paths, store)

    return asyncio.run(work())


def call(name: str, arguments: dict[str, Any] | str, kind: str = "function") -> ToolCall:
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(f"call_{name}", name, text, kind="custom" if kind == "custom" else "function")


def query(table_id: str, selection: dict[str, list[str]], language: Language) -> ToolCall:
    return call("query_table", {"table_id": table_id, "selection": selection, "language": language})


def calculate(*steps: dict[str, Any]) -> ToolCall:
    return call(CALCULATE, {"steps": list(steps)})


def total(snapshot: str, year: str) -> dict[str, Any]:
    return {
        "name": f"total_{year}",
        "operation": "value",
        "snapshot": snapshot,
        "codes": {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": year},
    }


SEARCH = call("search_tables", {"query": "befolkning", "language": "no"})
METADATA = call("get_table_metadata", {"table_id": "05810", "language": "en"})
POPULATION_EN = query("05810", POPULATION, "en")


def view(outcome: ToolOutcome) -> dict[str, Any]:
    assert outcome.status == "result", outcome.text
    return cast("dict[str, Any]", json.loads(outcome.text))


def direct_search() -> SearchResult:
    async def work() -> SearchResult:
        async with make_client(BASE_URL, transport=REPLAY) as http:
            return await SsbClient(http, revision_period=False).search("befolkning", "no", 10)

    return asyncio.run(work())


def listed_tools() -> list[Tool]:
    async def work() -> list[Tool]:
        async with Client(build_server(transport=REPLAY, revision_period=False)) as client:
            return (await client.list_tools()).tools

    return asyncio.run(work())


def test_the_model_gets_the_three_server_tools_and_calculate(tmp_path: Path) -> None:
    schemas = run(tmp_path).schemas
    published = {tool.name: tool for tool in listed_tools()}

    assert [schema.name for schema in schemas] == [*SERVER_TOOLS, CALCULATE]
    for schema in schemas[:3]:
        tool = published[schema.name]
        assert (schema.description, dict(schema.parameters)) == (
            tool.description,
            tool.input_schema,
        )
    assert dict(schemas[3].parameters) == calculate_schema()
    assert tool_param(schemas[3])["function"]["name"] == "calculate"


def test_a_tool_that_the_server_adds_is_not_offered_or_run(tmp_path: Path) -> None:
    extra = Tool(name="delete_snapshots", input_schema={"type": "object"})
    toolbox = Toolbox(cast("Client", None), [*listed_tools(), extra], SnapshotStore(tmp_path), "no")

    assert "delete_snapshots" not in [schema.name for schema in toolbox.schemas]
    outcome = asyncio.run(toolbox.run(call("delete_snapshots", {})))
    assert outcome.status == "refused"
    assert outcome.text == (
        "The call was not run: there is no tool 'delete_snapshots'; the tools are "
        "search_tables, get_table_metadata, query_table, calculate"
    )


def test_a_server_without_one_of_the_tools_is_refused(tmp_path: Path) -> None:
    tools = [tool for tool in listed_tools() if tool.name != "query_table"]

    with pytest.raises(ValueError, match="the MCP server has no tool query_table"):
        Toolbox(cast("Client", None), tools, SnapshotStore(tmp_path), "no")


def test_calculate_publishes_the_plan_schema_with_evidence_ids() -> None:
    schema = calculate_schema()
    definitions = cast("dict[str, dict[str, Any]]", schema["$defs"])
    reading = sorted(
        name for name, definition in definitions.items() if "snapshot" in definition["properties"]
    )

    assert reading == ["MeanStep", "RankStep", "SumStep", "ValueStep"]
    for name in reading:
        assert definitions[name]["properties"]["snapshot"] == EVIDENCE_ID
    assert not SHA256.search(json.dumps(schema).replace("[0-9a-f]{64}", ""))
    assert "^[0-9a-f]{64}$" not in json.dumps(schema)
    restored = json.loads(json.dumps(schema))
    for name in reading:
        restored["$defs"][name]["properties"]["snapshot"] = plan_schema()["$defs"][name][
            "properties"
        ]["snapshot"]
    assert restored == plan_schema()


def test_a_search_result_gets_an_evidence_id_and_reaches_the_model_whole(
    tmp_path: Path,
) -> None:
    result = run(tmp_path, SEARCH)
    [outcome] = result.outcomes
    expected = direct_search()

    assert outcome.evidence == SourceEvidence("E1", expected)
    assert result.evidence == (SourceEvidence("E1", expected),)
    assert view(outcome) == {"evidence_id": "E1", **expected.model_dump(mode="json")}


@pytest.mark.parametrize("language", ["no", "en"])
def test_values_reach_the_model_as_decimal_strings_and_formatted(
    tmp_path: Path, language: Language
) -> None:
    result = run(tmp_path, POPULATION_EN, language=language)
    [outcome] = result.outcomes
    evidence = cast("SourceEvidence", outcome.evidence)
    cells = cast("QueryResult", evidence.result).observations
    shown = view(outcome)["observations"]

    assert len(shown) == len(cells) == 63
    for cell, item in zip(cells, shown, strict=True):
        assert cell.value is not None
        assert item == {
            "codes": list(cell.codes),
            "value": exact_text(cell.value),
            "symbol": None,
            "formatted": format_number(cell.value, language),
        }
    # The first cell is the population of Norway in 2024 in the recording.
    assert shown[0]["value"] == "5550203"
    assert (
        shown[0]["formatted"]
        == {"no": f"5{NO_BREAK_SPACE}550{NO_BREAK_SPACE}203", "en": "5,550,203"}[language]
    )


def test_decimals_are_formatted_and_missing_values_are_not(tmp_path: Path) -> None:
    result = run(tmp_path, query("06743", CATCH, "no"), query("06856", SYMBOLS, "en"))
    catch, symbols = (view(outcome)["observations"] for outcome in result.outcomes)

    assert [(cell["value"], cell["formatted"]) for cell in catch[:4]] == [
        ("12648", f"12{NO_BREAK_SPACE}648"),
        ("10205", f"10{NO_BREAK_SPACE}205"),
        ("2.7", "2,7"),
        ("2.4", "2,4"),
    ]
    missing = [cell for cell in symbols if cell["value"] is None]
    assert missing
    assert all("formatted" not in cell and cell["symbol"] for cell in missing)


def test_evidence_ids_count_results_in_order_and_skip_failures(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        SEARCH,
        call("get_table_metadata", {"table_id": "99999", "language": "en"}),
        METADATA,
        POPULATION_EN,
        calculate(total("E3", "2025")),
        calculate(total("E3", "2026")),
    )

    assert [outcome.status for outcome in result.outcomes] == [
        "result",
        "failed",
        "result",
        "result",
        "result",
        "result",
    ]
    assert [
        (evidence.evidence_id, type(evidence.result).__name__) for evidence in result.evidence
    ] == [
        ("E1", "SearchResult"),
        ("E2", "TableMetadata"),
        ("E3", "QueryResult"),
        ("C1", "CalculationResult"),
        ("C2", "CalculationResult"),
    ]
    assert [view(outcome)["evidence_id"] for outcome in result.outcomes if outcome.evidence] == [
        "E1",
        "E2",
        "E3",
        "C1",
        "C2",
    ]
    assert isinstance(result.evidence[1].result, TableMetadata)


def test_a_tool_error_from_the_server_reaches_the_model(tmp_path: Path) -> None:
    result = run(tmp_path, call("get_table_metadata", {"table_id": "99999", "language": "en"}))
    [outcome] = result.outcomes

    assert outcome.status == "failed"
    assert outcome.evidence is None
    assert outcome.text.startswith("The tool failed: ")
    assert "SSB has no table 99999" in outcome.text
    assert result.evidence == ()


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (call("search_tables", {"query": "x"}, kind="custom"), "only function calls are run"),
        (call("search_table", {"query": "x"}), "there is no tool 'search_table'"),
        (call("search_tables", '{"query": "x"'), "the arguments are not valid JSON"),
        (call("search_tables", '["befolkning"]'), "the arguments are not a JSON object"),
        (call("search_tables", '{"query": "x", "query": "y"}'), "name query more than once"),
        (call("search_tables", '{"query": "x", "limit": NaN}'), "hold NaN, which is not a number"),
        (call("search_tables", "{}"), "'query' is a required property"),
        (call("search_tables", ""), "'query' is a required property"),
        (call("search_tables", {"query": "x", "top": 3}), "search_tables takes no argument top"),
        (
            call("search_tables", {"query": "x", "limit": "10"}),
            "limit: '10' is not of type 'integer'",
        ),
        (
            call("search_tables", '{"query": "x", "limit": 2.0}'),
            "limit: Decimal('2.0') is not of type 'integer'",
        ),
        (
            call("search_tables", {"query": "x", "limit": 51}),
            "limit: 51 is greater than the maximum of 50",
        ),
        (
            call("search_tables", {"query": "x", "language": "se"}),
            "language: 'se' is not one of ['no', 'en']",
        ),
        (
            call("get_table_metadata", {"table_id": "5810"}),
            "table_id: '5810' does not match '^[0-9]{5}$'",
        ),
        (
            call("query_table", {"table_id": "05810", "selection": {"Tid": "2026"}}),
            "selection/Tid: '2026' is not of type 'array'",
        ),
        (call(CALCULATE, {"steps": []}), "steps: [] should be non-empty"),
        (
            calculate({**total("E1", "2026"), "snapshot": "a" * 64}),
            "steps/0/snapshot: '" + "a" * 64 + "' does not match '^E[1-9][0-9]*$'",
        ),
        (
            calculate({**total("E1", "2026"), "operation": "avg"}),
            "steps/0/operation: 'avg' is not one of add, change_percent, divide, mean, "
            "multiply, rank, share_percent, subtract, sum, value",
        ),
        (
            calculate(
                {key: value for key, value in total("E1", "2026").items() if key != "operation"}
            ),
            "steps/0: 'operation' is a required property",
        ),
        (
            calculate({key: value for key, value in total("E1", "2026").items() if key != "codes"}),
            "steps/0: 'codes' is a required property",
        ),
        (
            calculate(
                total("E1", "2026"),
                {"name": "both", "operation": "add", "left": "a", "right": "b", "decimals": 2},
            ),
            "steps/1: Additional properties are not allowed ('decimals' was unexpected)",
        ),
        (
            calculate(
                {
                    "name": "largest",
                    "operation": "rank",
                    "snapshot": "E1",
                    "selection": {"Tid": [2026]},
                    "by": "Alder",
                    "order": "highest",
                    "count": 2,
                }
            ),
            "steps/0/selection/Tid/0: 2026 is not of type 'string'",
        ),
        (call(CALCULATE, {"steps": "total"}), "steps: 'total' is not of type 'array'"),
        (
            call("search_tables", "[" * 100_000 + "]" * 100_000),
            "the arguments are nested too deeply",
        ),
        (
            call("search_tables", '{"query": ' + "[" * 100_000 + "]" * 100_000 + "}"),
            "the arguments are nested too deeply",
        ),
        (
            call("search_tables", '{"query": ' + "[" * 20 + "]" * 20 + "}"),
            "the arguments are nested too deeply",
        ),
        (
            call("search_tables", '{"query": ' + "[" * 19 + "]" * 19 + "}"),
            "query: [[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]] is not of type 'string'",
        ),
        (
            call("get_table_metadata", {"table_id": "05810\n"}),
            "the arguments hold a control character, such as a line break",
        ),
        (
            query("05810", {"Tid\t": ["2026"]}, "en"),
            "the arguments hold a control character, such as a line break",
        ),
        (
            query("05810", {"Tid": ["2026\r"]}, "en"),
            "the arguments hold a control character, such as a line break",
        ),
    ],
)
def test_a_call_that_does_not_match_a_published_schema_is_not_run(
    tmp_path: Path, bad: ToolCall, reason: str
) -> None:
    result = run(tmp_path, bad)
    [outcome] = result.outcomes

    assert outcome.status == "refused"
    assert outcome.evidence is None
    assert outcome.text.startswith("The call was not run: ")
    assert reason in outcome.text
    assert result.paths == []
    assert result.evidence == ()


def engine_run(store: SnapshotStore, steps: Sequence[dict[str, Any]]) -> CalculationResult:
    return Engine(store).run(Plan.model_validate_json(json.dumps({"steps": steps})))


@pytest.mark.parametrize("language", ["no", "en"])
def test_calculate_replaces_evidence_ids_with_snapshot_ids(
    tmp_path: Path, language: Language
) -> None:
    rank = {
        "name": "largest",
        "operation": "rank",
        "snapshot": "E1",
        "selection": {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
        "by": "Alder",
        "order": "highest",
        "count": 2,
    }
    change = {
        "name": "growth",
        "operation": "change_percent",
        "old": "total_2025",
        "new": "total_2026",
        "decimals": 2,
    }
    steps = [total("E1", "2025"), total("E1", "2026"), change, rank]
    result = run(tmp_path, POPULATION_EN, calculate(*steps), language=language)
    query_outcome, outcome = result.outcomes
    snapshot_id = cast("QueryResult", cast("SourceEvidence", query_outcome.evidence).result)
    snapshot_id = snapshot_id.provenance.response_sha256
    expected = engine_run(
        result.store,
        [{**step, "snapshot": snapshot_id} if "snapshot" in step else step for step in steps],
    )

    assert outcome.evidence == CalculationEvidence("C1", expected)
    shown = view(outcome)
    assert shown["evidence_id"] == "C1"
    assert [step.get("read") for step in shown["steps"]] == ["E1", "E1", None, "E1"]
    for item, step in zip(shown["steps"], expected.steps, strict=True):
        assert item["value"] == (exact_text(step.value) if step.value is not None else None)
        if step.value is not None:
            assert item["formatted"] == format_number(step.value, language)
        assert [cell["formatted"] for cell in item["ranked"]] == [
            format_number(cell.value, language) for cell in step.ranked
        ]
    assert (
        shown["steps"][1]["formatted"]
        == {
            "no": f"5{NO_BREAK_SPACE}627{NO_BREAK_SPACE}400",
            "en": "5,627,400",
        }[language]
    )
    assert len(shown["steps"][3]["ranked"]) == 2


def test_calculate_names_a_result_that_it_cannot_read(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        SEARCH,
        calculate(total("E1", "2026")),
        calculate(total("E7", "2026")),
    )

    assert [outcome.text for outcome in result.outcomes[1:]] == [
        "The tool failed: E1 is not a query_table result, so it has no cells",
        "The tool failed: E7 is not a result of this run",
    ]
    assert [outcome.status for outcome in result.outcomes[1:]] == ["failed", "failed"]


def test_calculate_reports_a_query_result_without_a_snapshot(tmp_path: Path) -> None:
    result = run(tmp_path, POPULATION_EN, calculate(total("E1", "2026")), keep=False)

    assert result.outcomes[1].text == (
        "The tool failed: E1 has no snapshot, so calculate cannot read it"
    )
    assert result.evidence[-1].evidence_id == "E1"


def test_an_engine_error_names_the_evidence_id_and_not_the_snapshot(tmp_path: Path) -> None:
    def damage(store: SnapshotStore) -> None:
        [directory] = [path for path in store.directory.iterdir() if path.is_dir()]
        (directory / "observations.parquet").write_bytes(b"changed")

    result = run(tmp_path, POPULATION_EN, damage, calculate(total("E1", "2026")))
    outcome = result.outcomes[1]

    assert outcome.status == "failed"
    assert outcome.text == (
        "The tool failed: Step 'total_2026' reads the snapshot of E1, which cannot be read"
    )


def test_a_local_path_in_a_store_error_stays_out_of_the_tool_message(tmp_path: Path) -> None:
    def lock(store: SnapshotStore) -> None:
        [directory] = [path for path in store.directory.iterdir() if path.is_dir()]
        (directory / "snapshot.json").chmod(0)

    result = run(tmp_path, POPULATION_EN, lock, calculate(total("E1", "2026")))
    outcome = result.outcomes[1]

    assert outcome.text == (
        "The tool failed: Step 'total_2026' reads the snapshot of E1, which cannot be read"
    )
    assert str(tmp_path) not in outcome.text
    assert not SHA256.search(outcome.text)


def test_an_engine_error_about_codes_reaches_the_model(tmp_path: Path) -> None:
    step = total("E1", "2026") | {"codes": {"Kjonn": "9"}}
    result = run(tmp_path, POPULATION_EN, calculate(step))

    assert result.outcomes[1].text == (
        "The tool failed: Step 'total_2026' needs codes for Alder, ContentsCode, Tid"
    )


def test_the_plan_model_checks_a_plan_after_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The schema and the plan model agree on every plan found so far; the model still
    # refuses a plan if they ever differ. Here the schema check is skipped.
    def unchecked(schema: ToolSchema, arguments: Mapping[str, Any]) -> None:
        return None

    monkeypatch.setattr(tools, "check_arguments", unchecked)
    step = total("E1", "2026") | {"name": "total 2026"}
    result = run(tmp_path, POPULATION_EN, calculate(step))
    outcome = result.outcomes[1]

    assert outcome.status == "refused"
    assert outcome.text.startswith(
        "The call was not run: the plan is not valid: steps/0/value/name: String should "
        "match pattern '^[A-Za-z][A-Za-z0-9_]{0,39}$'"
    )
    assert result.evidence[-1].evidence_id == "E1"


def test_two_query_results_in_one_plan_keep_their_own_snapshots(tmp_path: Path) -> None:
    catch = {
        "name": "catch_2016",
        "operation": "value",
        "snapshot": "E2",
        "codes": {
            "FiskeslagSamiske": "80a",
            "SamiskeElver": "18",
            "ContentsCode": "Fangst",
            "Tid": "2016",
        },
    }
    steps = [total("E1", "2026"), catch]
    result = run(tmp_path, POPULATION_EN, query("06743", CATCH, "no"), calculate(*steps))
    first, second = (
        cast("QueryResult", cast("SourceEvidence", outcome.evidence).result)
        for outcome in result.outcomes[:2]
    )
    expected = engine_run(
        result.store,
        [
            steps[0] | {"snapshot": first.provenance.response_sha256},
            steps[1] | {"snapshot": second.provenance.response_sha256},
        ],
    )

    assert first.provenance.response_sha256 != second.provenance.response_sha256
    assert result.outcomes[2].evidence == CalculationEvidence("C1", expected)
    shown = view(result.outcomes[2])
    assert [(step["read"], step["value"]) for step in shown["steps"]] == [
        ("E1", "5627400"),
        ("E2", "10205"),
    ]


class Answering:
    """An MCP client stand-in whose every call returns the same result."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        return self.result


@pytest.mark.parametrize("content", [None, {"total": "many", "tables": []}])
def test_a_server_result_that_cannot_be_read_fails_the_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: dict[str, Any] | None
) -> None:
    client = Answering(CallToolResult(content=[], structured_content=content))
    toolbox = Toolbox(cast("Client", client), listed_tools(), SnapshotStore(tmp_path), "no")

    outcome = asyncio.run(toolbox.run(SEARCH))

    assert outcome.status == "failed"
    assert outcome.text == "The tool failed: the result of search_tables could not be read"
    assert toolbox.evidence == ()
    assert "The result of search_tables could not be read" in caplog.text


@pytest.mark.parametrize(("language", "words"), [("no", "befolkning"), ("en", "population")])
def test_a_call_without_a_language_gets_the_language_of_the_answer(
    tmp_path: Path, language: Language, words: str
) -> None:
    async def direct() -> SearchResult:
        async with make_client(BASE_URL, transport=REPLAY) as http:
            return await SsbClient(http, revision_period=False).search(words, language, 10)

    result = run(tmp_path, call("search_tables", {"query": words}), language=language)
    [outcome] = result.outcomes

    assert view(outcome) == {"evidence_id": "E1", **asyncio.run(direct()).model_dump(mode="json")}


def test_the_model_reads_dimension_values_as_codes_and_labels(tmp_path: Path) -> None:
    result = run(tmp_path, METADATA)
    [outcome] = result.outcomes
    metadata = cast("TableMetadata", cast("SourceEvidence", outcome.evidence).result)
    shown = view(outcome)

    for item, dimension in zip(shown["dimensions"], metadata.dimensions, strict=True):
        assert item["values"] == {value.code: value.label for value in dimension.values}
        notes = {value.code: list(value.notes) for value in dimension.values if value.notes}
        assert item.get("value_notes", {}) == notes
        assert (item["code"], item["label"], item["time"]) == (
            dimension.code,
            dimension.label,
            dimension.time,
        )
    # The evidence for the gate keeps every field.
    assert outcome.evidence is not None
    assert result.evidence[0].result == metadata


def test_notes_of_a_value_stay_beside_the_compact_values() -> None:
    from opencited.agent.tools import compact_dimension

    dimension: dict[str, Any] = {
        "code": "Region",
        "label": "region",
        "values": [
            {"code": "0", "label": "Hele landet", "notes": []},
            {"code": "3101", "label": "Halden", "notes": ["Ny kommune fra 2024."]},
        ],
        "notes": [],
        "time": False,
    }

    assert compact_dimension(dimension) == {
        "code": "Region",
        "label": "region",
        "values": {"0": "Hele landet", "3101": "Halden"},
        "value_notes": {"3101": ["Ny kommune fra 2024."]},
        "notes": [],
        "time": False,
    }


@pytest.mark.parametrize(
    ("value", "scale", "norwegian", "english"),
    [
        ("1382", 3, "1\u00a0382\u00a0000", "1,382,000"),
        ("2.5", 3, "2500", "2500"),
        ("5511334", 6, "5\u00a0511\u00a0334 millioner", "5,511,334 million"),
        ("3.25", 9, "3,25 milliarder", "3.25 billion"),
        ("104.0", 0, "104,0", "104.0"),
        # More digits than the default decimal context keeps.
        ("1" * 30, 3, None, "111,111,111,111,111,111,111,111,111,111,000"),
    ],
)
def test_a_value_is_formatted_in_its_base_unit(
    value: str, scale: int, norwegian: str | None, english: str
) -> None:
    if norwegian is not None:
        assert formatted(Decimal(value), scale, "no") == norwegian
    assert formatted(Decimal(value), scale, "en") == english


def test_a_query_result_in_thousands_reaches_the_model_in_persons() -> None:
    selection = {
        "Alder": ["15-74"],
        "Kjonn": ["2", "1"],
        "ContentsCode": ["Sysselsatte"],
        "Tid": ["2026K2"],
    }
    result = retrieve("03778", selection, "no").result

    view = source_view("E1", result, "no")

    cells = cast("list[dict[str, Any]]", view["observations"])
    assert [(cell["value"], cell["formatted"]) for cell in cells] == [
        ("1382", "1\u00a0382\u00a0000"),
        ("1553", "1\u00a0553\u00a0000"),
    ]


def test_a_per_cent_cell_reaches_the_model_as_written_whatever_its_unit_s_name_holds() -> None:
    unit = Unit(code="Personer", base="Prosent av BNP, mill.", decimals=0)
    result = population().result.model_copy(update={"units": (unit,)})

    view = source_view("E1", result, "en")

    cells = cast("list[dict[str, Any]]", view["observations"])
    shown = [(cell["value"], cell["formatted"]) for cell in cells if cell.get("value")]
    assert shown
    assert all(formatted == format_number(Decimal(value), "en") for value, formatted in shown)


def test_a_rounded_mean_in_thousands_reaches_the_model_in_a_form_the_gate_passes(
    tmp_path: Path,
) -> None:
    # 03778 gives 1382 and 1553 in 1 000 personer; their mean, 1467.5, rounds to 1468.
    selection = {
        "Alder": ["15-74"],
        "Kjonn": ["2", "1"],
        "ContentsCode": ["Sysselsatte"],
        "Tid": ["2026K2"],
    }
    store = SnapshotStore(tmp_path)
    retrieval = retrieve("03778", selection, "no")
    snapshot = store.write(retrieval).name
    step = {
        "name": "mean",
        "operation": "mean",
        "snapshot": snapshot,
        "selection": selection,
        "decimals": 0,
    }
    result = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": [step]})))
    assert result.step("mean").value == Decimal(1468)

    view = calculation_view("C1", result, {}, "no")
    shown = cast("list[dict[str, Any]]", view["steps"])[0]["formatted"]
    report = check(
        f"I snitt var {shown} personer sysselsatt [C1].", "no", [CalculationEvidence("C1", result)]
    )

    assert shown == "1\u00a0467\u00a0500"
    assert report.passed, report.text()
