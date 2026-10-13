"""Contract tests: the SSB client against responses recorded from the live API.

Every request goes through the replay transport, so a request that no recording answers
fails the test instead of reaching SSB. The expected results are derived from the
recorded bodies, cell codes included, and the structure checks are tested on recorded
bodies with one part changed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import httpx2
import pytest
from recorded import (
    FIXTURES,
    Json,
    codes_in_order,
    description,
    get,
    raw,
    recorded,
    recorded_cells,
    value_tokens,
)

from opencited.http import make_client
from opencited.http.replay import UnrecordedRequestError, replay_transport
from opencited.sources import (
    DataResult,
    InvalidResponseError,
    Language,
    RejectedRequestError,
    Retrieved,
    SourceUnavailableError,
    TableMetadata,
    TableNotFoundError,
)
from opencited.sources.ssb import (
    BASE_URL,
    MAX_GET_URL_LENGTH,
    SsbClient,
    data_request,
    parse_data,
    parse_metadata,
    url_codes,
)

TRANSPORT = replay_transport(FIXTURES)

SYMBOLS = {
    "RegionSamisk": ["1849", "1850", "1853"],
    "ContentsCode": ["*"],
    "Tid": ["2008", "2009", "2010"],
}
DECIMALS = {
    "FiskeslagSamiske": ["80a"],
    "SamiskeElver": ["18", "18000000127", "18144000000"],
    "ContentsCode": ["Fangst", "Gjennomsnitt"],
    "Tid": ["2015", "2016"],
}
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}
EXPRESSIONS = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}
POSTED = {
    "Kjonn": ["1", "2"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2024", "2025", "2026"],
}
DATA: list[tuple[str, Mapping[str, Sequence[str]], Language, str]] = [
    ("06856", SYMBOLS, "en", "data-06856-symbols-en"),
    ("06743", DECIMALS, "no", "data-06743-decimals-no"),
    ("05810", POPULATION, "no", "data-05810-no"),
    ("05810", POPULATION, "en", "data-05810-en"),
    ("05810", EXPRESSIONS, "en", "data-05810-expressions-en"),
]


def run[T](
    action: Callable[[SsbClient], Awaitable[T]], max_get_url_length: int = MAX_GET_URL_LENGTH
) -> T:
    """Run one call of the client against the recordings."""

    async def call() -> T:
        async with make_client(BASE_URL, transport=TRANSPORT) as http:
            return await action(SsbClient(http, max_get_url_length=max_get_url_length))

    return asyncio.run(call())


def data(
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language = "en",
    max_get_url_length: int = MAX_GET_URL_LENGTH,
) -> Retrieved[DataResult]:
    return run(lambda client: client.data(table_id, selection, language), max_get_url_length)


def check_described(result: TableMetadata | DataResult, body: Json, table_id: str) -> None:
    """Check every field that metadata and data share against a recorded body."""
    role = cast("dict[str, list[str]]", body["role"])
    assert result.table_id == table_id
    assert result.title == cast("str", body["label"]).removeprefix(f"{table_id}: ")
    assert result.updated == datetime.fromisoformat(cast("str", body["updated"]))
    assert result.source == body["source"]
    assert result.notes == tuple(cast("list[str]", body.get("note") or []))
    assert [dimension.code for dimension in result.dimensions] == body["id"]
    units: list[tuple[str, str, Decimal]] = []
    for dimension in result.dimensions:
        described = cast("Json", get(body, "dimension", dimension.code))
        category = cast("Json", described["category"])
        labels = cast("dict[str, str]", category["label"])
        notes = cast("dict[str, list[str]]", category.get("note") or {})
        extension = cast("Json", described.get("extension") or {})
        assert dimension.label == described["label"]
        assert [value.code for value in dimension.values] == codes_in_order(category)
        assert [value.label for value in dimension.values] == [
            labels[v.code] for v in dimension.values
        ]
        assert [value.notes for value in dimension.values] == [
            tuple(notes.get(v.code, [])) for v in dimension.values
        ]
        assert dimension.notes == tuple(cast("list[str]", described.get("note") or []))
        assert dimension.time is (dimension.code in role["time"])
        assert dimension.contents is (dimension.code in role["metric"])
        assert dimension.eliminable is (extension.get("elimination") is True)
        for code, unit in cast("dict[str, Json]", category.get("unit") or {}).items():
            units.append((code, cast("str", unit["base"]), cast("Decimal", unit["decimals"])))
    assert [(unit.code, unit.base, Decimal(unit.decimals)) for unit in result.units] == units


# Recorded responses.


def test_config() -> None:
    config = run(lambda client: client.config())
    body = recorded("config")
    assert config.api_version == body["apiVersion"]
    assert config.app_version == body["appVersion"]
    assert config.max_data_cells == body["maxDataCells"]
    assert config.languages == ("no", "en")
    assert config.licence_url == body["license"]


@pytest.mark.parametrize(
    ("query", "language", "name"),
    [("befolkning", "no", "search-befolkning-no"), ("population", "en", "search-population-en")],
)
def test_search(query: str, language: Language, name: str) -> None:
    found = run(lambda client: client.search(query, language, 10))
    body = recorded(name)
    tables = cast("list[Json]", body["tables"])
    assert found.total == get(body, "page", "totalElements")
    assert len(found.tables) == len(tables)
    for summary, table in zip(found.tables, tables, strict=True):
        assert summary.table_id == table["id"]
        assert summary.title == cast("str", table["label"]).removeprefix(f"{table['id']}: ")
        assert summary.first_period == table["firstPeriod"]
        assert summary.last_period == table["lastPeriod"]
        assert summary.updated == datetime.fromisoformat(cast("str", table["updated"]))
        assert summary.time_unit == table.get("timeUnit")
        assert summary.variables == tuple(cast("list[str]", table["variableNames"]))


@pytest.mark.parametrize("table_id", ["06856", "06743", "05810"])
@pytest.mark.parametrize("language", ["no", "en"])
def test_metadata(table_id: str, language: Language) -> None:
    metadata = run(lambda client: client.metadata(table_id, language))
    check_described(metadata, recorded(f"metadata-{table_id}-{language}"), table_id)


def test_metadata_keeps_the_notes_on_single_codes() -> None:
    metadata = run(lambda client: client.metadata("05810", "en"))
    years = next(dimension for dimension in metadata.dimensions if dimension.time)
    noted = {value.code for value in years.values if value.notes}
    assert noted == set(
        cast("Json", get(recorded("metadata-05810-en"), "dimension", "Tid", "category", "note"))
    )
    assert noted


@pytest.mark.parametrize(("table_id", "selection", "language", "name"), DATA)
def test_data(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str
) -> None:
    result = data(table_id, selection, language).value
    check_described(result, recorded(name), table_id)
    assert [
        (cell.codes, str(cell.value) if cell.value is not None else "null", cell.symbol)
        for cell in result.observations
    ] == recorded_cells(name)


def test_data_keeps_symbols_and_the_notes_that_explain_them() -> None:
    result = data("06856", SYMBOLS).value
    assert {cell.symbol for cell in result.observations} == {None, ".."}
    assert all(cell.value is None for cell in result.observations if cell.symbol)
    assert any(note.startswith(".. = ") for note in result.notes)


def test_data_keeps_the_written_scale_of_decimals() -> None:
    result = data("06743", DECIMALS, "no").value
    tokens = value_tokens("data-06743-decimals-no")
    assert [str(cell.value) for cell in result.observations] == tokens
    assert any("." in token for token in tokens)


def test_data_is_the_same_in_both_languages() -> None:
    norwegian = data("05810", POPULATION, "no").value
    english = data("05810", POPULATION, "en").value
    assert [cell.value for cell in norwegian.observations] == [
        cell.value for cell in english.observations
    ]
    assert norwegian.dimensions[0].label != english.dimensions[0].label


def test_defaults_are_norwegian_and_ten_tables() -> None:
    assert run(lambda client: client.search("befolkning")).total == get(
        recorded("search-befolkning-no"), "page", "totalElements"
    )
    assert run(lambda client: client.metadata("05810")).title == title("metadata-05810-no", "05810")
    assert run(lambda client: client.data("05810", POPULATION)).value.title == title(
        "data-05810-no", "05810"
    )


def title(name: str, table_id: str) -> str:
    return cast("str", recorded(name)["label"]).removeprefix(f"{table_id}: ")


def test_the_exchange_is_the_one_recorded() -> None:
    before = datetime.now(UTC)
    retrieved = data("05810", POPULATION)
    after = datetime.now(UTC)
    request = cast("Json", description("data-05810-en")["request"])
    assert retrieved.exchange.method == "GET"
    assert retrieved.exchange.url == request["url"]
    assert retrieved.exchange.body is None
    assert retrieved.exchange.status == 200
    assert retrieved.exchange.content == raw("data-05810-en")
    assert before <= retrieved.exchange.retrieved_at <= after
    assert retrieved.exchange.content_type.startswith("application/json")


def test_long_selections_go_as_post() -> None:
    retrieved = data("05810", POSTED, max_get_url_length=0)
    request = cast("Json", description("data-05810-post-en")["request"])
    assert retrieved.exchange.method == "POST"
    assert retrieved.exchange.url == request["url"]
    assert retrieved.exchange.body == cast("str", request["body"]).encode()
    assert [
        (cell.codes, str(cell.value), cell.symbol) for cell in retrieved.value.observations
    ] == recorded_cells("data-05810-post-en")


# Recorded errors.


def test_an_unknown_table_has_no_metadata() -> None:
    with pytest.raises(TableNotFoundError, match="SSB has no table 99999"):
        run(lambda client: client.metadata("99999", "en"))


def test_an_unknown_table_has_no_data_although_ssb_answers_400() -> None:
    assert cast("Json", description("error-data-99999-en")["response"])["status"] == 400
    with pytest.raises(TableNotFoundError, match="SSB has no table 99999"):
        data("99999", {"Tid": ["2026"]})


@pytest.mark.parametrize(
    ("selection", "name"),
    [
        ({"Tid": ["2026"]}, "error-data-05810-missing-variable-en"),
        (
            {"Kjonn": ["0"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["1800"]},
            "error-data-05810-unknown-value-en",
        ),
        (
            {"Fylke": ["03"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
            "error-data-05810-unknown-variable-en",
        ),
    ],
)
def test_rejected_selections_carry_ssb_s_reason(
    selection: Mapping[str, Sequence[str]], name: str
) -> None:
    reason = cast("str", recorded(name)["title"])
    with pytest.raises(RejectedRequestError, match=re.escape(reason)):
        data("05810", selection)


def test_an_html_error_page_is_unavailable() -> None:
    selection = {
        "Kjonn": ["0"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2026"] * 450,
    }
    with pytest.raises(SourceUnavailableError, match="HTTP 404 and no JSON body"):
        data("05810", selection, max_get_url_length=10_000)


def test_a_request_without_a_recording_fails() -> None:
    with pytest.raises(UnrecordedRequestError):
        run(lambda client: client.metadata("12345", "en"))


# Input that is rejected before any request. A request would reach the replay transport,
# which has no recording for these, so the test would fail with a different error.


@pytest.mark.parametrize("table_id", ["123", "123456", "abcde", "0585 ", "٠٥٨١٠"])
def test_invalid_table_ids_are_rejected(table_id: str) -> None:
    with pytest.raises(ValueError, match="five digits"):
        run(lambda client: client.metadata(table_id, "en"))
    with pytest.raises(ValueError, match="five digits"):
        data(table_id, POPULATION)


def test_unknown_languages_are_rejected() -> None:
    other = cast("Language", "de")
    with pytest.raises(ValueError, match="language"):
        run(lambda client: client.search("befolkning", other))
    with pytest.raises(ValueError, match="language"):
        run(lambda client: client.metadata("05810", other))
    with pytest.raises(ValueError, match="language"):
        data("05810", POPULATION, other)


def test_an_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        run(lambda client: client.search("  "))


@pytest.mark.parametrize("limit", [0, 51])
def test_search_limits_are_checked(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        run(lambda client: client.search("befolkning", "no", limit))


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ({}, "at least one dimension"),
        ({"Tid": []}, "non-empty"),
        ({"Tid": [""]}, "non-empty"),
        ({"Tid": "2026"}, "not a string"),
        ({"Tid": ["[top(3)]"]}, "without brackets"),
    ],
)
def test_invalid_selections_are_rejected(
    selection: Mapping[str, Sequence[str]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        data("05810", selection)


def test_a_client_without_ssb_s_base_url_is_refused() -> None:
    with pytest.raises(ValueError, match="base URL"):
        SsbClient(httpx2.AsyncClient())


# Building requests.


def test_codes_with_commas_are_wrapped_in_brackets() -> None:
    assert url_codes(["2020", "range(2020,2023)", "*"]) == "2020,[range(2020,2023)],*"


def test_the_switch_to_post_follows_the_url_length() -> None:
    client = httpx2.AsyncClient(base_url=BASE_URL)
    short = data_request(client, "05810", {"Tid": ["2026"]}, "en")
    long = data_request(client, "05810", {"Tid": ["2026"] * 300}, "en")
    assert short.method == "GET"
    assert len(str(short.url)) < MAX_GET_URL_LENGTH
    assert long.method == "POST"
    assert json.loads(long.content) == {
        "selection": [{"variableCode": "Tid", "valueCodes": ["2026"] * 300}]
    }


# Structure checks, on recorded bodies with one part changed.


def changed(name: str, change: Callable[[Json], None]) -> Json:
    body = copy.deepcopy(recorded(name))
    change(body)
    return body


def test_the_order_of_index_keys_does_not_matter() -> None:
    def reverse_years(body: Json) -> None:
        category = cast("Json", get(body, "dimension", "Tid", "category"))
        category["index"] = dict(reversed(cast("dict[str, Decimal]", category["index"]).items()))

    original = parse_metadata(recorded("metadata-05810-en"), "05810", "en")
    assert parse_metadata(changed("metadata-05810-en", reverse_years), "05810", "en") == original


def test_a_dimension_note_is_kept() -> None:
    def add_note(body: Json) -> None:
        cast("Json", get(body, "dimension", "Kjonn"))["note"] = ["A note on the dimension"]

    metadata = parse_metadata(changed("metadata-05810-en", add_note), "05810", "en")
    assert metadata.dimensions[0].notes == ("A note on the dimension",)


def first_index(body: Json) -> dict[str, Decimal]:
    return cast("dict[str, Decimal]", get(body, "dimension", "RegionSamisk", "category", "index"))


def leave_a_gap(body: Json) -> None:
    first_index(body)["1849"] = Decimal(5)


def change_a_size(body: Json) -> None:
    cast("list[Decimal]", body["size"])[0] = Decimal(4)


def drop_a_value(body: Json) -> None:
    cast("list[object]", body["value"]).pop()


def add_a_symbol_beyond_the_cells(body: Json) -> None:
    cast("dict[str, str]", body["status"])["999"] = ".."


def write_a_value_as_text(body: Json) -> None:
    cast("list[object]", body["value"])[1] = "86"


def drop_the_time_zone(body: Json) -> None:
    body["updated"] = "2022-02-10T07:00:00"


def make_a_size_absurd(body: Json) -> None:
    body["size"] = [Decimal("1E+30"), Decimal(4), Decimal(3)]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (leave_a_gap, "gaps"),
        (change_a_size, "sizes do not match"),
        (drop_a_value, "values for"),
        (add_a_symbol_beyond_the_cells, "not the index"),
        (write_a_value_as_text, "number or missing"),
        (drop_the_time_zone, "time zone"),
        (make_a_size_absurd, "whole number"),
    ],
)
def test_bodies_that_break_the_structure_are_refused(
    change: Callable[[Json], None], message: str
) -> None:
    with pytest.raises(InvalidResponseError, match=message):
        parse_data(changed("data-06856-symbols-en", change), "06856", "en")


def test_results_cannot_change() -> None:
    metadata = run(lambda client: client.metadata("06743", "en"))
    assert isinstance(metadata.units, tuple)
    with pytest.raises(ValueError, match="frozen"):
        metadata.title = "Another title"  # pyright: ignore[reportAttributeAccessIssue] - the test checks that assignment fails
    assert metadata.unit("Gjennomsnitt") is not None
    assert metadata.unit("Fangst") is not None
    assert metadata.unit("Nothing") is None


def test_results_hold_no_float() -> None:
    def floats(value: object) -> list[object]:
        if isinstance(value, float):
            return [value]
        if isinstance(value, dict):
            return [
                f for item in cast("dict[object, object]", value).values() for f in floats(item)
            ]
        if isinstance(value, list | tuple):
            return [f for item in cast("list[object]", value) for f in floats(item)]
        return []

    for table_id, selection, language, _ in DATA:
        assert floats(data(table_id, selection, language).value.model_dump()) == []
