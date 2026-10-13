"""Property-based tests of JSON-stat parsing (spec 0001, verification 1).

The datasets are generated in the shape that SSB sends, and the expected codes of each
cell are worked out with divmod from the sizes, independently of the parser.
"""

from __future__ import annotations

import json
import math
import string
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from opencited.sources.ssb import parse_data
from opencited.values import load_json

CODES = st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=6)
VALUES = st.integers(min_value=0, max_value=6).flatmap(
    lambda places: st.decimals(
        min_value=Decimal("-1e15"),
        max_value=Decimal("1e15"),
        allow_nan=False,
        allow_infinity=False,
        places=places,
    )
)
SYMBOLS = st.sampled_from([".", "..", ":"])

type Dataset = tuple[list[tuple[str, list[str]]], list[Decimal | None], dict[int, str]]


@st.composite
def datasets(draw: st.DrawFn) -> Dataset:
    dimensions = [
        (f"D{number}", draw(st.lists(CODES, min_size=1, max_size=4, unique=True)))
        for number in range(draw(st.integers(min_value=1, max_value=3)))
    ]
    count = math.prod(len(codes) for _, codes in dimensions)
    values = draw(st.lists(st.one_of(st.none(), VALUES), min_size=count, max_size=count))
    symbols = {index: draw(SYMBOLS) for index, value in enumerate(values) if value is None}
    return dimensions, values, symbols


def as_json(dataset: Dataset) -> bytes:
    dimensions, values, symbols = dataset
    document: dict[str, object] = {
        "label": "00000: Generated",
        "source": "Generated",
        "updated": "2026-10-13T00:00:00Z",
        "id": [code for code, _ in dimensions],
        "size": [len(codes) for _, codes in dimensions],
        "role": {"time": [], "metric": []},
        "dimension": {
            code: {
                "label": code,
                "category": {
                    "index": {value: position for position, value in enumerate(codes)},
                    "label": {value: value for value in codes},
                },
            }
            for code, codes in dimensions
        },
        "status": {str(index): symbol for index, symbol in symbols.items()},
    }
    written = ",".join("null" if value is None else str(value) for value in values)
    return (json.dumps(document)[:-1] + f',"value":[{written}]}}').encode()


def codes_of(position: int, dimensions: list[tuple[str, list[str]]]) -> tuple[str, ...]:
    """Return the codes of the cell at ``position``, with the last dimension fastest."""
    codes: list[str] = []
    for _, values in reversed(dimensions):
        position, coordinate = divmod(position, len(values))
        codes.append(values[coordinate])
    return tuple(reversed(codes))


@settings(deadline=None)
@given(datasets())
def test_every_cell_gets_its_codes_value_and_symbol(dataset: Dataset) -> None:
    dimensions, values, symbols = dataset
    result = parse_data(load_json(as_json(dataset)), "00000")
    assert [cell.codes for cell in result.observations] == [
        codes_of(position, dimensions) for position in range(len(values))
    ]
    assert [None if cell.value is None else str(cell.value) for cell in result.observations] == [
        None if value is None else str(value) for value in values
    ]
    assert [cell.symbol for cell in result.observations] == [
        symbols.get(position) for position in range(len(values))
    ]
