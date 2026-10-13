"""Selections: codes and PxWebApi expressions expanded on recorded metadata, cells counted
before a data request, and SSB's revision period respected.

Expected codes are slices of the recorded metadata, in the order the PxWebApi guide
describes. The recordings of data with expressions show that the expansion matches the
cells that SSB returned.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx2
import pytest
from recorded import FIXTURES, raw, recorded_cells

from opencited.http import make_client
from opencited.http.replay import UnrecordedRequestError, load_recordings
from opencited.sources import (
    Dimension,
    InvalidResponseError,
    InvalidSelectionError,
    QueryResult,
    RevisionPeriodError,
    TableMetadata,
    TooManyCellsError,
)
from opencited.sources.pxweb import cell_count, expand, resolve
from opencited.sources.ssb import BASE_URL, SsbClient, in_revision_period, parse_metadata
from opencited.values import load_json

NOON = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
POPULATION = parse_metadata(load_json(raw("metadata-05810-en")), "05810")
FISH = parse_metadata(load_json(raw("metadata-06856-en")), "06856")


def dimension(metadata: TableMetadata, code: str) -> Dimension:
    return next(found for found in metadata.dimensions if found.code == code)


def codes(metadata: TableMetadata, code: str) -> list[str]:
    return [value.code for value in dimension(metadata, code).values]


YEARS = codes(POPULATION, "Tid")
AGES = codes(POPULATION, "Alder")


# Expressions on recorded metadata.


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (["*"], YEARS),
        (["top(3)"], YEARS[-3:]),
        (["top(2, 1)"], YEARS[-3:-1]),
        (["bottom(2)"], YEARS[:2]),
        (["bottom(2,1)"], YEARS[1:3]),
        (["range(2020,2023)"], YEARS[YEARS.index("2020") : YEARS.index("2023") + 1]),
        (["from(2024)"], YEARS[YEARS.index("2024") :]),
        (["to(1855)"], YEARS[: YEARS.index("1855") + 1]),
        (["2026", "2025", "top(1)"], ["2025", "2026"]),
        (["19??"], [year for year in YEARS if year.startswith("19") and len(year) == 4]),
    ],
)
def test_time_expressions(items: list[str], expected: list[str]) -> None:
    assert list(expand(dimension(POPULATION, "Tid"), items)) == expected


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (["top(2)"], AGES[:2]),
        (["bottom(2)"], AGES[-2:]),
        (["0?-??"], [age for age in AGES if len(age) == 5 and age[0] == "0" and age[2] == "-"]),
        (["*+"], [age for age in AGES if age.endswith("+")]),
        (["999B", "80+"], ["999B", "80+"]),
    ],
)
def test_expressions_on_other_dimensions(items: list[str], expected: list[str]) -> None:
    assert list(expand(dimension(POPULATION, "Alder"), items)) == expected


def test_masks_cover_codes_that_start_alike() -> None:
    regions = codes(FISH, "RegionSamisk")
    assert list(expand(dimension(FISH, "RegionSamisk"), ["18*"])) == [
        r for r in regions if r.startswith("18")
    ]


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (["1800"], "has no code '1800'. Its codes are 1845, 1850"),
        (["range(2023,2020)"], "ends before it starts"),
        (["from(1800)"], "has no code '1800'"),
    ],
)
def test_codes_that_the_table_lacks_are_refused(items: list[str], message: str) -> None:
    with pytest.raises(InvalidSelectionError, match=message):
        expand(dimension(POPULATION, "Tid"), items)


def test_an_error_lists_at_most_twenty_codes() -> None:
    with pytest.raises(InvalidSelectionError, match=f"and {len(YEARS) - 20} more"):
        expand(dimension(POPULATION, "Tid"), ["1800"])


# Resolving a selection against the table.


def test_a_left_out_dimension_that_can_be_eliminated_counts_once() -> None:
    resolved = resolve(POPULATION, {"ContentsCode": ["Personer"], "Tid": ["top(3)"]})
    assert resolved == {"ContentsCode": ("Personer",), "Tid": tuple(YEARS[-3:])}
    assert cell_count(resolved) == 3


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (
            {"Fylke": ["03"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
            "has no dimension Fylke. Its dimensions are Kjonn",
        ),
        ({"Kjonn": ["0"]}, "needs codes for ContentsCode, Tid"),
        ({"ContentsCode": ["Personer"], "Tid": ["19??9"]}, "selects no code"),
        ({"ContentsCode": ["Personer"], "Tid": ["top(0)"]}, "selects no code"),
    ],
)
def test_selections_that_do_not_fit_the_table_are_refused(
    selection: Mapping[str, Sequence[str]], message: str
) -> None:
    with pytest.raises(InvalidSelectionError, match=message):
        resolve(POPULATION, selection)


@pytest.mark.parametrize(
    ("metadata_name", "table_id", "selection", "data_name"),
    [
        (
            "metadata-05810-en",
            "05810",
            {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]},
            "data-05810-expressions-en",
        ),
        (
            "metadata-06856-en",
            "06856",
            {
                "RegionSamisk": ["1849", "1850", "1853"],
                "ContentsCode": ["*"],
                "Tid": ["2008", "2009", "2010"],
            },
            "data-06856-symbols-en",
        ),
        (
            "metadata-05810-en",
            "05810",
            {
                "Kjonn": ["0"],
                "Alder": ["999B"],
                "ContentsCode": ["Personer"],
                "Tid": ["2025", "2026"],
            },
            "data-05810-en",
        ),
    ],
)
def test_the_expansion_matches_the_cells_that_ssb_returned(
    metadata_name: str, table_id: str, selection: Mapping[str, Sequence[str]], data_name: str
) -> None:
    metadata = parse_metadata(load_json(raw(metadata_name)), table_id)
    resolved = resolve(metadata, selection)
    cells = recorded_cells(data_name)
    assert cell_count(resolved) == len(cells)
    for position, code in enumerate(resolved):
        assert list(dict.fromkeys(cell[0][position] for cell in cells)) == list(resolved[code])


# Queries: the checks happen before the data request.

RECORDINGS = load_recordings(FIXTURES)
EVERYTHING = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["*"]}


def recorded_transport(sent: list[str]) -> httpx2.MockTransport:
    """Answer from the recordings, and note the path of every request."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url.path)
        recorded = RECORDINGS.get((request.method, str(request.url), request.content or None))
        if recorded is None:
            raise UnrecordedRequestError(f"no recording for {request.method} {request.url}")
        return httpx2.Response(recorded.status, headers=recorded.headers, content=recorded.body)

    return httpx2.MockTransport(handle)


def query(
    selection: Mapping[str, Sequence[str]],
    transport: httpx2.MockTransport,
    *,
    now: datetime = NOON,
    max_cells: int = 5000,
) -> QueryResult:
    async def call() -> QueryResult:
        async with make_client(BASE_URL, transport=transport) as http:
            client = SsbClient(http, now=lambda: now, max_cells=max_cells)
            return await client.query("05810", selection, "en")

    return asyncio.run(call())


def test_too_many_cells_fail_before_a_data_request() -> None:
    sent: list[str] = []
    with pytest.raises(TooManyCellsError, match="Kjonn, Alder, which can be left out") as caught:
        query(EVERYTHING, recorded_transport(sent), max_cells=100)
    expected = len(codes(POPULATION, "Kjonn")) * len(AGES) * len(YEARS)
    assert caught.value.cells == expected
    assert caught.value.limit == 100
    assert f"{expected:,} cells" in str(caught.value)
    assert sent == ["/api/pxwebapi/v2/tables/05810/metadata"]


def test_an_unknown_code_fails_before_a_data_request() -> None:
    sent: list[str] = []
    with pytest.raises(InvalidSelectionError, match="'1800'"):
        query({"ContentsCode": ["Personer"], "Tid": ["1800"]}, recorded_transport(sent))
    assert sent == ["/api/pxwebapi/v2/tables/05810/metadata"]


def test_the_default_limit_is_five_thousand_cells() -> None:
    sent: list[str] = []
    result = query(
        {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]},
        recorded_transport(sent),
    )
    assert len(result.observations) == 63
    assert sent == ["/api/pxwebapi/v2/tables/05810/metadata", "/api/pxwebapi/v2/tables/05810/data"]


def test_no_request_is_sent_in_the_revision_period() -> None:
    sent: list[str] = []
    six_in_oslo = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    with pytest.raises(RevisionPeriodError, match="08:15"):
        query(EVERYTHING, recorded_transport(sent), now=six_in_oslo)
    assert sent == []


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        # 14 October 2026 is in summer time, two hours ahead of UTC.
        (datetime(2026, 10, 14, 2, 59, tzinfo=UTC), False),
        (datetime(2026, 10, 14, 3, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 14, 6, 14, tzinfo=UTC), True),
        (datetime(2026, 10, 14, 6, 15, tzinfo=UTC), False),
        # 14 January 2027 is in winter time, one hour ahead of UTC.
        (datetime(2027, 1, 14, 3, 59, tzinfo=UTC), False),
        (datetime(2027, 1, 14, 4, 0, tzinfo=UTC), True),
        (datetime(2027, 1, 14, 7, 14, tzinfo=UTC), True),
        (datetime(2027, 1, 14, 7, 15, tzinfo=UTC), False),
    ],
)
def test_the_revision_period_follows_norwegian_time(moment: datetime, inside: bool) -> None:
    assert in_revision_period(moment) is inside


def test_a_reply_with_other_cells_than_counted_is_refused() -> None:
    """The data recorded for top(3) is served for a request for top(2)."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        name = (
            "metadata-05810-en"
            if request.url.path.endswith("/metadata")
            else "data-05810-expressions-en"
        )
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=raw(name))

    selection = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(2)"]}
    with pytest.raises(InvalidResponseError, match="63 cells for a selection of 42"):
        query(selection, httpx2.MockTransport(handle))


def test_a_selection_at_the_limit_is_allowed() -> None:
    selection = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}
    assert len(query(selection, recorded_transport([]), max_cells=63).observations) == 63
    with pytest.raises(TooManyCellsError):
        query(selection, recorded_transport([]), max_cells=62)
