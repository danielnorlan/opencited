"""Selections: codes and PxWebApi expressions expanded on recorded metadata, cells counted
before a data request, and SSB's revision period respected.

The recordings cover `*`, masks with `?`, `top` with and without an offset, `bottom` on
a dimension that is not time, `range` combined with `from`, left-out dimensions and codes
in another order than the metadata. `to` and the remaining combinations rest on the
PxWebApi guide; their tests use slices of the recorded metadata.
"""

from __future__ import annotations

import asyncio
import pickle
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

import httpx2
import pytest
from recorded import FIXTURES, Json, description, get, raw, recorded, recorded_cells

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
    Value,
)
from opencited.sources.pxweb import cell_count, expand, glob, normalise, normalised, resolve
from opencited.sources.ssb import BASE_URL, MAX_CELLS, SsbClient, in_revision_period, parse_metadata
from opencited.values import load_json

NOON = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
POPULATION = parse_metadata(load_json(raw("metadata-05810-en")), "05810", "en")
FISH = parse_metadata(load_json(raw("metadata-06856-en")), "06856", "en")


def dimension(metadata: TableMetadata, code: str) -> Dimension:
    return next(found for found in metadata.dimensions if found.code == code)


def codes(metadata: TableMetadata, code: str) -> list[str]:
    return [value.code for value in dimension(metadata, code).values]


YEARS = codes(POPULATION, "Tid")
AGES = codes(POPULATION, "Alder")
SEXES = codes(POPULATION, "Kjonn")


def made_up(*values: str) -> Dimension:
    """Return a dimension written by the test, to reach shapes that no table shows."""
    return Dimension(
        code="X",
        label="x",
        values=tuple(Value(code=value, label=value, notes=()) for value in values),
        notes=(),
        time=False,
        contents=False,
        eliminable=True,
    )


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
        (["range( 2020 , 2023 )"], YEARS[YEARS.index("2020") : YEARS.index("2023") + 1]),
        (["from(2024)"], YEARS[YEARS.index("2024") :]),
        (["to(1855)"], YEARS[: YEARS.index("1855") + 1]),
        (["to( 1855 )"], YEARS[: YEARS.index("1855") + 1]),
        (["2026", "2025", "top(1)"], ["2025", "2026"]),
        (["19??"], [year for year in YEARS if year.startswith("19") and len(year) == 4]),
        (["top( 3 )", " top(3) "], YEARS[-3:]),
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
        (["80+", "999B"], ["999B", "80+"]),
    ],
)
def test_expressions_on_other_dimensions(items: list[str], expected: list[str]) -> None:
    assert list(expand(dimension(POPULATION, "Alder"), items)) == expected


def test_masks_cover_codes_that_start_alike() -> None:
    regions = codes(FISH, "RegionSamisk")
    assert list(expand(dimension(FISH, "RegionSamisk"), ["18*"])) == [
        region for region in regions if region.startswith("18")
    ]


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        ("1*", ["1", "12", "123"]),
        ("*1", ["1", "x1"]),
        ("1?", ["12"]),
        ("?", ["1"]),
        ("1?3", ["123"]),
        ("*", ["1", "12", "123", "x1"]),
        ("**1*", ["1", "12", "123", "x1"]),
    ],
)
def test_masks_follow_the_guide(mask: str, expected: list[str]) -> None:
    assert list(expand(made_up("1", "12", "123", "x1"), [mask])) == expected


def test_a_long_mask_is_quick() -> None:
    started = time.perf_counter()
    assert not glob("*" * 60 + "?x", "1" * 60)
    assert glob("*" * 60 + "1", "1" * 60)
    assert time.perf_counter() - started < 0.5


# Refused items, each named in the error.


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (["1800"], "Tid has no code '1800'"),
        (["range(2023,2020)"], "'range(2023,2020)' ends before it starts"),
        (["from(1800)"], "'from(1800)' names the code '1800', which Tid lacks"),
        (["top(three)"], "'top(three)' is not a valid expression; the forms are top(n)"),
        (["TOP(3)"], "'TOP(3)' is not a valid expression"),
        (["top()"], "'top()' is not a valid expression"),
        (["19??9"], "'19??9' selects no code in Tid"),
        (["top(0)"], "'top(0)' selects no code in Tid"),
        (["top(3,60)"], "'top(3,60)' selects no code in Tid"),
        (["2026", "19??9"], "'19??9' selects no code in Tid"),
    ],
)
def test_items_that_select_nothing_valid_are_refused(items: list[str], message: str) -> None:
    with pytest.raises(InvalidSelectionError, match=re.escape(message)):
        expand(dimension(POPULATION, "Tid"), items)


def test_a_huge_number_in_an_expression_is_refused() -> None:
    with pytest.raises(InvalidSelectionError, match="is not a valid expression"):
        expand(dimension(POPULATION, "Tid"), ["top(" + "9" * 5000 + ")"])


def test_an_error_lists_the_latest_periods_and_where_the_rest_are() -> None:
    with pytest.raises(InvalidSelectionError) as caught:
        expand(dimension(POPULATION, "Tid"), ["2027"])
    message = str(caught.value)
    assert f"{len(YEARS) - 20} earlier periods and {', '.join(YEARS[-20:])}" in message
    assert "metadata lists them all" in message


def test_an_error_lists_other_codes_from_the_start() -> None:
    regions = codes(FISH, "RegionSamisk")
    listed = f"{', '.join(regions[:20])} and {len(regions) - 20} more"
    with pytest.raises(InvalidSelectionError, match=re.escape(listed)):
        expand(dimension(FISH, "RegionSamisk"), ["0000"])


def test_arguments_with_commas_or_parentheses_are_not_expressions() -> None:
    with pytest.raises(InvalidSelectionError, match="not a valid expression"):
        expand(made_up("a(1)", "b", "c,d"), ["range(a(1),b)"])
    assert list(expand(made_up("a(1)", "b", "c,d"), ["a(1)", "c,d"])) == ["a(1)", "c,d"]


# Normalised items, as they are sent.


@pytest.mark.parametrize(
    ("item", "sent"),
    [
        ("top( 3 )", "top(3)"),
        (" top(2 , 1) ", "top(2,1)"),
        ("range( 2020 , 2022 )", "range(2020,2022)"),
        ("from( 2025)", "from(2025)"),
        ("to(1855 )", "to(1855)"),
        ("19??", "19??"),
        ("2026", "2026"),
    ],
)
def test_expressions_are_sent_without_spaces(item: str, sent: str) -> None:
    assert normalised(item) == sent


def test_repeated_items_are_sent_once() -> None:
    assert normalise({"Tid": ["2026", "top( 1 )", "2026", "top(1)"]}) == {"Tid": ["2026", "top(1)"]}


# Resolving a selection against the table.


def test_a_left_out_dimension_that_can_be_eliminated_counts_once() -> None:
    resolved = resolve(POPULATION, {"ContentsCode": ["Personer"], "Tid": ["top(3)"]})
    assert resolved == {"ContentsCode": ("Personer",), "Tid": tuple(YEARS[-3:])}
    assert cell_count(resolved) == 3


def test_every_problem_of_a_selection_is_reported_at_once() -> None:
    selection = {"Fylke": ["03"], "Kjonn": ["9"], "Alder": ["999B"], "Tid": ["2026"]}
    with pytest.raises(InvalidSelectionError) as caught:
        resolve(POPULATION, selection)
    message = str(caught.value)
    assert (
        "Table 05810 has no dimension Fylke; its dimensions are Kjonn, Alder, ContentsCode, Tid"
        in message
    )
    assert "needs codes for ContentsCode, which cannot be left out" in message
    assert "Kjonn has no code '9'" in message
    assert "metadata lists every dimension and code" in message


EXPRESSIONS: list[tuple[str, Mapping[str, Sequence[str]]]] = [
    (
        "data-05810-expressions-en",
        {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]},
    ),
    (
        "data-05810-offset-en",
        {"Kjonn": ["0"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["top(2,1)"]},
    ),
    (
        "data-05810-bottom-en",
        {"Kjonn": ["0"], "Alder": ["bottom(2)"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
    ),
    (
        "data-05810-masks-en",
        {"Kjonn": ["0"], "Alder": ["0?-??"], "ContentsCode": ["Personer"], "Tid": ["202?"]},
    ),
    (
        "data-05810-range-en",
        {
            "Kjonn": ["0"],
            "Alder": ["999B"],
            "ContentsCode": ["Personer"],
            "Tid": ["range(2020,2022)", "from(2025)"],
        },
    ),
    ("data-05810-eliminated-en", {"ContentsCode": ["Personer"], "Tid": ["2026"]}),
    (
        "data-05810-order-en",
        {"Kjonn": ["1", "2"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
    ),
    (
        "data-05810-post-en",
        {
            "Kjonn": ["1", "2"],
            "Alder": ["999B"],
            "ContentsCode": ["Personer"],
            "Tid": ["2024", "2025", "2026"],
        },
    ),
]


def returned_codes(name: str) -> dict[str, set[str]]:
    """Return the codes that a recorded data body holds in each of its dimensions."""
    body = recorded(name)
    return {
        code: set(cast("Json", get(body, "dimension", code, "category", "index")))
        for code in cast("list[str]", body["id"])
    }


@pytest.mark.parametrize(("name", "selection"), EXPRESSIONS, ids=[name for name, _ in EXPRESSIONS])
def test_the_expansion_matches_the_cells_that_ssb_returned(
    name: str, selection: Mapping[str, Sequence[str]]
) -> None:
    resolved = resolve(POPULATION, selection)
    assert {code: set(found) for code, found in resolved.items()} == returned_codes(name)
    assert cell_count(resolved) == len(recorded_cells(name))


def test_ssb_returns_cells_in_the_order_of_the_request() -> None:
    index = cast(
        "Json", get(recorded("data-05810-order-en"), "dimension", "Kjonn", "category", "index")
    )
    assert list(index) == ["1", "2"]
    assert SEXES.index("2") < SEXES.index("1")


# Queries: the checks happen before the data request.

RECORDINGS = load_recordings(FIXTURES)
EVERYTHING = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["*"]}
METADATA = "/tables/05810/metadata?lang=en"


def recorded_transport(sent: list[str]) -> httpx2.MockTransport:
    """Answer from the recordings, and note every request with its query."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url).removeprefix(BASE_URL))
        found = RECORDINGS.get((request.method, str(request.url), request.content or None))
        if found is None:
            raise UnrecordedRequestError(f"no recording for {request.method} {request.url}")
        return httpx2.Response(found.status, headers=found.headers, content=found.body)

    return httpx2.MockTransport(handle)


def query(
    selection: Mapping[str, Sequence[str]],
    transport: httpx2.MockTransport,
    *,
    now: datetime = NOON,
    max_cells: int = MAX_CELLS,
) -> QueryResult:
    async def call() -> QueryResult:
        async with make_client(BASE_URL, transport=transport) as http:
            client = SsbClient(http, now=lambda: now, max_cells=max_cells)
            return await client.query("05810", selection, "en")

    return asyncio.run(call())


def test_too_many_cells_fail_before_a_data_request() -> None:
    sent: list[str] = []
    with pytest.raises(TooManyCellsError) as caught:
        query(EVERYTHING, recorded_transport(sent), max_cells=100)
    expected = len(SEXES) * len(AGES) * len(YEARS)
    message = str(caught.value)
    assert caught.value.cells == expected
    assert caught.value.limit == 100
    assert (
        f"{expected:,} cells (codes per dimension: Kjonn: {len(SEXES)}, Alder: {len(AGES)}"
        in message
    )
    assert "top(n) in Tid" in message
    assert (
        "leaving out Kjonn or Alder, for which SSB then gives one value, usually the total"
        in message
    )
    assert sent == [METADATA]


def test_the_advice_fits_the_dimensions_of_the_selection() -> None:
    with pytest.raises(TooManyCellsError) as caught:
        query({"ContentsCode": ["Personer"], "Tid": ["*"]}, recorded_transport([]), max_cells=10)
    assert "leaving out" not in str(caught.value)
    assert "top(n) in Tid" in str(caught.value)


def test_the_default_limit_is_five_thousand_cells() -> None:
    async def call() -> int:
        async with make_client(BASE_URL) as http:
            return SsbClient(http).max_cells

    assert MAX_CELLS == 5000
    assert asyncio.run(call()) == 5000


def test_a_selection_at_the_limit_is_allowed() -> None:
    selection = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}
    assert len(query(selection, recorded_transport([]), max_cells=63).observations) == 63
    with pytest.raises(TooManyCellsError):
        query(selection, recorded_transport([]), max_cells=62)


def test_an_unknown_code_fails_before_a_data_request() -> None:
    sent: list[str] = []
    with pytest.raises(InvalidSelectionError, match="'1800'"):
        query({"ContentsCode": ["Personer"], "Tid": ["1800"]}, recorded_transport(sent))
    assert sent == [METADATA]


def test_a_selection_of_the_wrong_form_sends_nothing() -> None:
    sent: list[str] = []
    with pytest.raises(ValueError, match="not a string"):
        query({"Tid": "2026"}, recorded_transport(sent))
    assert sent == []


def test_expressions_are_normalised_before_they_are_sent() -> None:
    sent: list[str] = []
    selection = {
        "Kjonn": ["*"],
        "Alder": ["*", "*"],
        "ContentsCode": ["Personer"],
        "Tid": ["top( 3 )"],
    }
    result = query(selection, recorded_transport(sent))
    request = cast("Json", description("data-05810-expressions-en")["request"])
    assert sent[-1] == cast("str", request["url"]).removeprefix(BASE_URL)
    assert len(result.observations) == 63


def test_codes_in_another_order_than_the_metadata_are_accepted() -> None:
    selection = {
        "Kjonn": ["1", "2"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2026"],
    }
    result = query(selection, recorded_transport([]))
    assert [cell.codes[0] for cell in result.observations] == ["1", "2"]


def test_no_request_is_sent_in_the_revision_period() -> None:
    sent: list[str] = []
    six_in_oslo = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    with pytest.raises(RevisionPeriodError, match=re.escape("until 08:00. Try again after 08:15")):
        query(EVERYTHING, recorded_transport(sent), now=six_in_oslo)
    assert sent == []


def test_a_clock_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="time zone"):
        query(EVERYTHING, recorded_transport([]), now=datetime(2026, 10, 14, 12, 0))


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


def serving(data_name: str) -> httpx2.MockTransport:
    """Serve the recorded metadata, and one recorded data body for any data request."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        name = "metadata-05810-en" if request.url.path.endswith("/metadata") else data_name
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=raw(name))

    return httpx2.MockTransport(handle)


@pytest.mark.parametrize(
    ("selection", "served"),
    [
        (
            {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(2)"]},
            "data-05810-expressions-en",
        ),
        (
            {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["bottom(3)"]},
            "data-05810-expressions-en",
        ),
        ({"ContentsCode": ["Personer"], "Tid": ["2026"]}, "data-05810-en"),
    ],
    ids=["fewer periods", "other periods", "a dimension that was left out"],
)
def test_a_reply_with_other_cells_than_the_selection_is_refused(
    selection: Mapping[str, Sequence[str]], served: str
) -> None:
    with pytest.raises(InvalidResponseError, match="other cells than the table's metadata gives"):
        query(selection, serving(served))


def test_the_cell_limit_error_survives_pickling() -> None:
    error = TooManyCellsError("too many", 6000, 5000)
    again = pickle.loads(pickle.dumps(error))  # noqa: S301 - the test unpickles its own object
    assert (str(again), again.cells, again.limit) == ("too many", 6000, 5000)
