"""Provenance and symbols of query results, against responses recorded from the live API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from opencited.http import Exchange, make_client
from opencited.http.replay import replay_transport
from opencited.provenance import record_of, sha256_hex
from opencited.sources import Language, QueryResult, Symbol
from opencited.sources.ssb import BASE_URL, SsbClient
from opencited.values import load_json

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ssb"
TRANSPORT = replay_transport(FIXTURES)
type Json = dict[str, object]

QUERIES: list[tuple[str, Mapping[str, Sequence[str]], Language, str, int]] = [
    (
        "06856",
        {
            "RegionSamisk": ["1849", "1850", "1853"],
            "ContentsCode": ["*"],
            "Tid": ["2008", "2009", "2010"],
        },
        "en",
        "data-06856-symbols-en",
        1800,
    ),
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
    (
        "05810",
        {"Kjonn": ["0"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["2025", "2026"]},
        "no",
        "data-05810-no",
        1800,
    ),
    (
        "05810",
        {"Kjonn": ["0"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["2025", "2026"]},
        "en",
        "data-05810-en",
        1800,
    ),
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


def query(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, limit: int
) -> QueryResult:
    async def call() -> QueryResult:
        async with make_client(BASE_URL, transport=TRANSPORT) as http:
            return await SsbClient(http, max_get_url_length=limit).query(
                table_id, selection, language
            )

    return asyncio.run(call())


def description(name: str) -> Json:
    return cast(
        "Json", json.loads((FIXTURES / f"{name}.2026-10-13.json").read_text(encoding="utf-8"))
    )


def body(name: str) -> Json:
    return cast("Json", load_json((FIXTURES / f"{name}.2026-10-13.body").read_bytes()))


@pytest.mark.parametrize(("table_id", "selection", "language", "name", "limit"), QUERIES, ids=IDS)
def test_every_record_matches_its_recording(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    before = datetime.now(UTC)
    record = query(table_id, selection, language, limit).provenance
    after = datetime.now(UTC)
    recorded = description(name)
    request = cast("Json", recorded["request"])
    response = cast("Json", recorded["response"])
    stored = (FIXTURES / str(response["body_file"])).read_bytes()
    assert record.response_sha256 == response["body_sha256"] == hashlib.sha256(stored).hexdigest()
    assert record.request.method == request["method"]
    assert record.request.url == request["url"]
    assert record.request.body == request["body"]
    assert before <= record.retrieved_at <= after
    assert record.retrieved_at.tzinfo is not None


@pytest.mark.parametrize(("table_id", "selection", "language", "name", "limit"), QUERIES, ids=IDS)
def test_every_record_names_the_source_and_the_table(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    result = query(table_id, selection, language, limit)
    record = result.provenance
    recorded = body(name)
    page = (
        "https://www.ssb.no/statbank/table/"
        if language == "no"
        else "https://www.ssb.no/en/statbank/table/"
    )
    assert record.source_id == "ssb"
    assert record.publisher == "Statistics Norway (SSB)"
    assert record.license == "CC-BY-4.0"
    assert record.table_id == table_id
    assert record.table_title == cast("str", recorded["label"]).removeprefix(f"{table_id}: ")
    assert record.table_url == page + table_id
    assert record.table_updated == datetime.fromisoformat(cast("str", recorded["updated"]))
    assert result.cached is False


def test_symbols_carry_ssb_s_explanation() -> None:
    table_id, selection, language, name, limit = QUERIES[0]
    result = query(table_id, selection, language, limit)
    notes = cast("list[str]", body(name)["note"])
    explanation = next(note for note in notes if note.startswith(".. = ")).removeprefix(".. = ")
    assert result.symbols == (Symbol(symbol="..", explanation=explanation),)
    assert result.notes == tuple(notes)


@pytest.mark.parametrize(
    ("table_id", "selection", "language", "name", "limit"), QUERIES[1:], ids=IDS[1:]
)
def test_results_without_symbols_explain_none(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    result = query(table_id, selection, language, limit)
    assert all(cell.symbol is None for cell in result.observations)
    assert result.symbols == ()


def floats(value: object) -> list[object]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [
            found for item in cast("dict[object, object]", value).values() for found in floats(item)
        ]
    if isinstance(value, list | tuple):
        return [found for item in cast("list[object]", value) for found in floats(item)]
    return []


def refuse_float(text: str) -> Decimal:
    raise AssertionError(f"the JSON form holds the float {text}")


@pytest.mark.parametrize(("table_id", "selection", "language", "name", "limit"), QUERIES, ids=IDS)
def test_no_value_is_a_float(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, name: str, limit: int
) -> None:
    result = query(table_id, selection, language, limit)
    assert floats(result.model_dump()) == []
    serialised = json.loads(result.model_dump_json(), parse_float=refuse_float)
    values = [
        cell["value"] for cell in cast("list[Json]", cast("Json", serialised)["observations"])
    ]
    assert all(value is None or isinstance(value, str) for value in values)


def test_a_record_describes_the_exchange_it_is_built_from() -> None:
    content = (FIXTURES / "data-05810-post-en.2026-10-13.body").read_bytes()
    request = cast("Json", description("data-05810-post-en")["request"])
    exchange = Exchange(
        method="POST",
        url=cast("str", request["url"]),
        body=cast("str", request["body"]).encode(),
        status=200,
        headers=(("content-type", "application/json"),),
        content=content,
        retrieved_at=datetime(2026, 10, 13, 21, 55, tzinfo=UTC),
    )
    record = record_of(
        exchange,
        source_id="ssb",
        publisher="Statistics Norway (SSB)",
        license="CC-BY-4.0",
        table_id="05810",
        table_title="Population, by sex and age 1845-2026",
        table_url="https://www.ssb.no/en/statbank/table/05810",
        table_updated=datetime(2026, 2, 25, 7, tzinfo=UTC),
    )
    assert record.request.body == request["body"]
    assert record.retrieved_at == exchange.retrieved_at
    assert record.response_sha256 == sha256_hex(content) == hashlib.sha256(content).hexdigest()
