"""Query results against responses recorded from the live API: provenance, symbols and
cells. The stored bodies are the hashed ones, since the recorder removes SSB's contact
details before it writes a body (ADR 0008).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import httpx2
import pytest
from pydantic import ValidationError
from recorded import FIXTURES, Json, description, raw, recorded, recorded_cells, value_tokens

from opencited.http import Exchange, make_client
from opencited.http.replay import replay_transport
from opencited.provenance import ProvenanceRecord, RequestRecord, record_of, sha256_hex
from opencited.sources import DataResult, Language, Observation, QueryResult, Symbol
from opencited.sources.ssb import BASE_URL, SsbClient, explained_symbols

TRANSPORT = replay_transport(FIXTURES)
SYMBOLS = {
    "RegionSamisk": ["1849", "1850", "1853"],
    "ContentsCode": ["*"],
    "Tid": ["2008", "2009", "2010"],
}
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}

QUERIES: list[tuple[str, Mapping[str, Sequence[str]], Language, str, int]] = [
    ("06856", SYMBOLS, "en", "data-06856-symbols-en", 1800),
    ("06856", SYMBOLS, "no", "data-06856-symbols-no", 1800),
    (
        "06743",
        {
            "FiskeslagSamiske": ["80a"],
            "SamiskeElver": ["18", "18000000127", "18144000000"],
            "ContentsCode": ["Fangst", "Gjennomsnitt"],
            "Tid": ["2015", "2016"],
        },
        "no",
        "data-06743-decimals-no",
        1800,
    ),
    ("05810", POPULATION, "no", "data-05810-no", 1800),
    ("05810", POPULATION, "en", "data-05810-en", 1800),
    (
        "05810",
        {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]},
        "en",
        "data-05810-expressions-en",
        1800,
    ),
    (
        "05810",
        {
            "Kjonn": ["1", "2"],
            "Alder": ["999B"],
            "ContentsCode": ["Personer"],
            "Tid": ["2024", "2025", "2026"],
        },
        "en",
        "data-05810-post-en",
        0,
    ),
]
IDS = [query[3] for query in QUERIES]
PARAMETERS = ("table_id", "selection", "language", "name", "limit")


def query(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, limit: int
) -> QueryResult:
    async def call() -> QueryResult:
        async with make_client(BASE_URL, transport=TRANSPORT) as http:
            return await SsbClient(http, max_get_url_length=limit).query(
                table_id, selection, language
            )

    return asyncio.run(call())


def plain_data(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, limit: int
) -> DataResult:
    async def call() -> DataResult:
        async with make_client(BASE_URL, transport=TRANSPORT) as http:
            client = SsbClient(http, max_get_url_length=limit)
            return (await client.data(table_id, selection, language)).value

    return asyncio.run(call())


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_every_record_matches_its_recording(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    before = datetime.now(UTC)
    record = query(table_id, selection, language, limit).provenance
    after = datetime.now(UTC)
    request = cast("Json", description(name)["request"])
    response = cast("Json", description(name)["response"])
    assert record.response_sha256 == response["body_sha256"] == sha256_hex(raw(name))
    assert record.request == RequestRecord(
        method=cast("str", request["method"]),
        url=cast("str", request["url"]),
        body=cast("str | None", request["body"]),
    )
    assert before <= record.retrieved_at <= after
    assert record.retrieved_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_every_record_names_the_source_and_the_table(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    result = query(table_id, selection, language, limit)
    record = result.provenance
    body = recorded(name)
    page = (
        "https://www.ssb.no/statbank/table/"
        if language == "no"
        else "https://www.ssb.no/en/statbank/table/"
    )
    assert record.source_id == "ssb"
    assert record.publisher == "Statistics Norway (SSB)"
    assert record.license == "CC-BY-4.0"
    assert record.table_id == table_id
    assert record.table_title == cast("str", body["label"]).removeprefix(f"{table_id}: ")
    assert record.table_url == page + table_id
    assert record.table_updated == datetime.fromisoformat(cast("str", body["updated"]))
    assert result.cached is False


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_query_returns_the_recorded_cells(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    result = query(table_id, selection, language, limit)
    plain = plain_data(table_id, selection, language, limit)
    assert [
        (c.codes, "null" if c.value is None else str(c.value), c.symbol)
        for c in result.observations
    ] == recorded_cells(name)
    assert result.observations == plain.observations
    assert result.dimensions == plain.dimensions
    assert result.units == plain.units
    assert result.notes == plain.notes


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_the_json_form_writes_every_value_as_ssb_did(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    def refuse_float(text: str) -> Decimal:
        raise AssertionError(f"the JSON form holds the float {text}")

    result = query(table_id, selection, language, limit)
    serialised = cast("Json", json.loads(result.model_dump_json(), parse_float=refuse_float))
    values = [cell["value"] for cell in cast("list[Json]", serialised["observations"])]
    assert ["null" if value is None else value for value in values] == value_tokens(name)


@pytest.mark.parametrize(
    ("language", "name"), [("en", "data-06856-symbols-en"), ("no", "data-06856-symbols-no")]
)
def test_symbols_carry_ssb_s_explanation_in_both_languages(language: Language, name: str) -> None:
    result = query("06856", SYMBOLS, language, 1800)
    notes = cast("list[str]", recorded(name)["note"])
    assert result.symbols == (Symbol(symbol="..", explanation=notes[-1][len(".. = ") :]),)
    assert notes[-1].startswith(".. = ")
    assert result.notes == tuple(notes)


def test_results_without_symbols_list_none() -> None:
    result = query("05810", POPULATION, "en", 1800)
    assert all(cell.symbol is None for cell in result.observations)
    assert result.symbols == ()


# Symbols, on cells built by the tests.


def cells(*symbols: str | None) -> list[Observation]:
    return [
        Observation(codes=(str(number),), value=None, symbol=symbol)
        for number, symbol in enumerate(symbols)
    ]


def test_symbols_appear_in_the_order_the_cells_first_use_them() -> None:
    notes = [": = Confidential.", ". = Not applicable.", ".. = Not available."]
    assert explained_symbols(cells(None, ":", "..", ":", "."), notes) == (
        Symbol(symbol=":", explanation="Confidential."),
        Symbol(symbol="..", explanation="Not available."),
        Symbol(symbol=".", explanation="Not applicable."),
    )


def test_a_symbol_without_a_note_has_no_explanation() -> None:
    assert explained_symbols(cells(":"), [".. = Not available."]) == (
        Symbol(symbol=":", explanation=None),
    )


def test_the_first_note_for_a_symbol_wins_and_dots_are_told_apart() -> None:
    notes = [".. = First.", ".. = Second.", ". = One dot."]
    assert explained_symbols(cells("..", "."), notes) == (
        Symbol(symbol="..", explanation="First."),
        Symbol(symbol=".", explanation="One dot."),
    )
    assert explained_symbols(cells("."), [".. = Two dots."]) == (
        Symbol(symbol=".", explanation=None),
    )


# The record itself.


def exchange() -> Exchange:
    return Exchange(
        method="GET",
        url="https://data.ssb.no/api/pxwebapi/v2/tables/05810/data",
        body=None,
        status=200,
        headers=(("content-type", "application/json"),),
        content=raw("data-05810-en"),
        retrieved_at=datetime(2026, 10, 13, 21, 55, tzinfo=UTC),
    )


def record(
    *, table_id: str = "05810", table_updated: datetime = datetime(2026, 2, 25, 7, tzinfo=UTC)
) -> ProvenanceRecord:
    return record_of(
        exchange(),
        source_id="ssb",
        publisher="Statistics Norway (SSB)",
        license="CC-BY-4.0",
        table_id=table_id,
        table_title="Population, by sex and age 1845-2026",
        table_url="https://www.ssb.no/en/statbank/table/05810",
        table_updated=table_updated,
    )


def test_a_record_describes_the_exchange_it_is_built_from() -> None:
    built = record()
    assert built.request == RequestRecord(method="GET", url=exchange().url, body=None)
    assert built.retrieved_at == exchange().retrieved_at
    assert built.response_sha256 == sha256_hex(raw("data-05810-en"))


@pytest.mark.parametrize("table_id", ["5810", "٠٥٨١٠"])
def test_a_record_refuses_a_table_id_that_is_not_five_digits(table_id: str) -> None:
    with pytest.raises(ValidationError):
        record(table_id=table_id)


def test_a_record_refuses_an_update_time_without_a_time_zone() -> None:
    with pytest.raises(ValidationError):
        record(table_updated=datetime(2026, 2, 25, 7))


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 10, 13, 21, 55),
        datetime(2026, 10, 13, 23, 55, tzinfo=timezone(timedelta(hours=2))),
    ],
)
def test_the_retrieval_time_must_be_utc(moment: datetime) -> None:
    built = record()
    with pytest.raises(ValidationError, match=r"UTC|timezone"):
        ProvenanceRecord.model_validate({**dict(built), "retrieved_at": moment})


def test_the_hash_must_be_sha256_in_hex() -> None:
    built = record()
    for digest in ("zz", built.response_sha256.upper(), built.response_sha256[:-1]):
        with pytest.raises(ValidationError):
            ProvenanceRecord.model_validate({**dict(built), "response_sha256": digest})


@pytest.mark.live
@pytest.mark.parametrize(
    "url", ["https://www.ssb.no/statbank/table/05810", "https://www.ssb.no/en/statbank/table/05810"]
)
def test_statbank_pages_for_tables_exist(url: str) -> None:
    assert httpx2.get(url, timeout=30.0).status_code == 200
