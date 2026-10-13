"""Contract tests: the SSB client against responses recorded from the live API.

Every request goes through the replay transport, so a request that no recording answers
fails the test instead of reaching SSB. Expected values are read from the recorded
bodies, never written by hand.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx2
import pytest

from opencited.http import make_client
from opencited.http.replay import UnrecordedRequestError, replay_transport
from opencited.sources import (
    DataResult,
    Language,
    Retrieved,
    SourceUnavailableError,
    TableMetadata,
    TableNotFoundError,
)
from opencited.sources.ssb import BASE_URL, MAX_GET_URL_LENGTH, SsbClient, data_request, get_codes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ssb"
TRANSPORT = replay_transport(FIXTURES)


def run[T](action: Callable[[SsbClient], Awaitable[T]], **options: int) -> T:
    """Run one call of the client against the recordings."""

    async def call() -> T:
        async with make_client(BASE_URL, transport=TRANSPORT) as http:
            return await action(SsbClient(http, **options))

    return asyncio.run(call())


def recorded(name: str) -> dict[str, object]:
    """Return a recorded body, parsed with decimals like the client parses it."""
    path = FIXTURES / f"{name}.2026-10-13.body"
    return cast("dict[str, object]", json.loads(path.read_bytes(), parse_float=Decimal))


def data(
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language = "en",
    **options: int,
) -> Retrieved[DataResult]:
    return run(lambda client: client.data(table_id, selection, language), **options)


def recorded_cells(name: str) -> list[tuple[Decimal | None, str | None]]:
    body = recorded(name)
    values = cast("list[Decimal | None]", body["value"])
    status = cast("dict[str, str]", body.get("status") or {})
    return [
        (Decimal(value) if value is not None else None, status.get(str(index)))
        for index, value in enumerate(values)
    ]


def floats_in(value: object) -> list[object]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [
            found
            for item in cast("dict[object, object]", value).values()
            for found in floats_in(item)
        ]
    if isinstance(value, list | tuple):
        return [found for item in cast("list[object]", value) for found in floats_in(item)]
    return []


def test_config() -> None:
    config = run(lambda client: client.config())
    assert config.api_version == "2.3.2"
    assert config.app_version.startswith("2.5.0")
    assert config.max_data_cells == 800_000
    assert config.languages == ("no", "en")


@pytest.mark.parametrize(
    ("query", "language", "name"),
    [("befolkning", "no", "search-befolkning-no"), ("population", "en", "search-population-en")],
)
def test_search(query: str, language: Language, name: str) -> None:
    found = run(lambda client: client.search(query, language, 10))
    tables = cast("list[dict[str, object]]", recorded(name)["tables"])
    assert [summary.table_id for summary in found] == [table["id"] for table in tables]
    assert [summary.title for summary in found] == [table["label"] for table in tables]
    assert all(summary.updated.tzinfo is not None for summary in found)


@pytest.mark.parametrize("table_id", ["06856", "06743", "05810"])
@pytest.mark.parametrize("language", ["no", "en"])
def test_metadata(table_id: str, language: Language) -> None:
    metadata: TableMetadata = run(lambda client: client.metadata(table_id, language))
    body = recorded(f"metadata-{table_id}-{language}")
    described = cast("dict[str, dict[str, object]]", body["dimension"])
    role = cast("dict[str, list[str]]", body["role"])
    assert metadata.title == body["label"]
    assert [dimension.code for dimension in metadata.dimensions] == body["id"]
    for dimension in metadata.dimensions:
        category = cast("dict[str, dict[str, object]]", described[dimension.code]["category"])
        assert [value.code for value in dimension.values] == list(category["index"])
        assert [value.label for value in dimension.values] == [
            category["label"][value.code] for value in dimension.values
        ]
        assert dimension.time is (dimension.code in role["time"])
        assert dimension.contents is (dimension.code in role["metric"])
    assert metadata.notes == tuple(cast("list[str]", body.get("note", [])))
    contents = described[role["metric"][0]]
    units = cast(
        "dict[str, dict[str, object]]", cast("dict[str, object]", contents["category"])["unit"]
    )
    assert {code: (unit.base, Decimal(unit.decimals)) for code, unit in metadata.units.items()} == {
        code: (unit["base"], unit["decimals"]) for code, unit in units.items()
    }


def test_data_keeps_symbols_and_their_note() -> None:
    selection = {
        "RegionSamisk": ["1849", "1850", "1853"],
        "ContentsCode": ["*"],
        "Tid": ["2008", "2009", "2010"],
    }
    result = data("06856", selection).value
    assert len(result.observations) == 36
    assert [(cell.value, cell.symbol) for cell in result.observations] == recorded_cells(
        "data-06856-symbols-en"
    )
    assert {cell.symbol for cell in result.observations} == {None, ".."}
    assert all(cell.value is None for cell in result.observations if cell.symbol)
    assert any(note.startswith(".. = ") for note in result.notes)


def test_data_keeps_decimals_exactly() -> None:
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18", "18000000127", "18144000000"],
        "ContentsCode": ["Fangst", "Gjennomsnitt"],
        "Tid": ["2015", "2016"],
    }
    result = data("06743", selection, "no").value
    values = [cell.value for cell in result.observations]
    assert values == [value for value, _ in recorded_cells("data-06743-decimals-no")]
    assert "2.7" in [str(value) for value in values]
    assert result.units["Gjennomsnitt"].decimals == 1


def test_data_is_the_same_in_both_languages() -> None:
    selection = {
        "Kjonn": ["0"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2025", "2026"],
    }
    norwegian = data("05810", selection, "no").value
    english = data("05810", selection, "en").value
    assert [cell.value for cell in norwegian.observations] == [
        cell.value for cell in english.observations
    ]
    assert norwegian.dimensions[0].label != english.dimensions[0].label


def test_data_with_selection_expressions() -> None:
    selection = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}
    result = data("05810", selection).value
    assert [len(dimension.values) for dimension in result.dimensions] == [3, 7, 1, 3]
    assert len(result.observations) == 63


def test_long_selections_go_as_post() -> None:
    selection = {
        "Kjonn": ["1", "2"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2024", "2025", "2026"],
    }
    retrieved = data("05810", selection, max_get_url_length=0)
    assert retrieved.exchange.method == "POST"
    assert [(cell.value, cell.symbol) for cell in retrieved.value.observations] == recorded_cells(
        "data-05810-post-en"
    )


def test_short_selections_go_as_get() -> None:
    selection = {
        "Kjonn": ["0"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2025", "2026"],
    }
    retrieved = data("05810", selection)
    assert retrieved.exchange.method == "GET"
    assert retrieved.exchange.status == 200
    assert retrieved.exchange.content == (FIXTURES / "data-05810-en.2026-10-13.body").read_bytes()


def test_an_unknown_table_raises_table_not_found() -> None:
    with pytest.raises(TableNotFoundError, match="99999"):
        run(lambda client: client.metadata("99999", "en"))


def test_an_html_error_page_raises_source_unavailable() -> None:
    selection = {
        "Kjonn": ["0"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2026"] * 450,
    }
    with pytest.raises(SourceUnavailableError, match="no JSON body"):
        data("05810", selection, max_get_url_length=10_000)


def test_results_hold_no_float() -> None:
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18", "18000000127", "18144000000"],
        "ContentsCode": ["Fangst", "Gjennomsnitt"],
        "Tid": ["2015", "2016"],
    }
    result = data("06743", selection, "no").value
    assert floats_in(result.model_dump()) == []


# Input that is rejected before any request is sent. A request would reach the replay
# transport, which has no recording for these, so the test would fail differently.


@pytest.mark.parametrize("table_id", ["123", "123456", "abcde", "0585 "])
def test_invalid_table_ids_are_rejected_before_a_request(table_id: str) -> None:
    with pytest.raises(ValueError, match="five digits"):
        run(lambda client: client.metadata(table_id, "en"))


@pytest.mark.parametrize("limit", [0, 51])
def test_search_limits_are_checked(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        run(lambda client: client.search("befolkning", "no", limit))


def test_empty_selections_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one code"):
        data("05810", {"Tid": []})


def test_a_request_without_a_recording_fails() -> None:
    with pytest.raises(UnrecordedRequestError):
        run(lambda client: client.metadata("12345", "en"))


# Building requests.


def test_codes_with_commas_are_wrapped_in_brackets() -> None:
    assert get_codes(["2020", "range(2020,2023)", "*"]) == "2020,[range(2020,2023)],*"


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
