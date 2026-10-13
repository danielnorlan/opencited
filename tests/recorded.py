"""Recorded SSB responses, read the way tests compare results with what SSB sent.

The expected values that tests derive from here come from the recordings in
`tests/fixtures/ssb/`, never from memory.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import cast

from opencited.values import load_json

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ssb"
type Json = dict[str, object]


def newest(name: str) -> Path:
    """Return the description of the newest recording with this name."""
    paths = sorted(FIXTURES.glob(f"{name}.*.json"))
    if not paths:
        raise LookupError(f"no recording named {name}")
    return paths[-1]


def description(name: str) -> Json:
    """Return the description of a recording: the request, the status and the hashes."""
    return cast("Json", json.loads(newest(name).read_text(encoding="utf-8")))


def raw(name: str) -> bytes:
    """Return the stored body of a recording."""
    response = cast("Json", description(name)["response"])
    return (FIXTURES / str(response["body_file"])).read_bytes()


def recorded(name: str) -> Json:
    """Return a stored body, parsed with decimals as the client parses it."""
    return cast("Json", load_json(raw(name)))


def get(document: object, *path: str) -> object:
    for key in path:
        document = cast("Json", document)[key]
    return document


def value_tokens(name: str) -> list[str]:
    """Return the values of a recorded data body exactly as SSB wrote them."""
    text = raw(name).decode()
    start = text.rindex('"value":[') + len('"value":[')
    return text[start : text.index("]", start)].split(",")


def codes_in_order(category: Json) -> list[str]:
    index = cast("dict[str, Decimal]", category["index"])
    return sorted(index, key=lambda code: index[code])


def recorded_cells(name: str) -> list[tuple[tuple[str, ...], str, str | None]]:
    """Return the codes, the written value and the symbol of every recorded cell.

    The codes of cell n come from its position, row-major with the last dimension
    fastest, worked out with divmod from the sizes.
    """
    body = recorded(name)
    ids = cast("list[str]", body["id"])
    sizes = [int(size) for size in cast("list[Decimal]", body["size"])]
    codes = [codes_in_order(cast("Json", get(body, "dimension", code, "category"))) for code in ids]
    status = cast("dict[str, str]", body.get("status") or {})
    cells: list[tuple[tuple[str, ...], str, str | None]] = []
    for position, token in enumerate(value_tokens(name)):
        rest = position
        coordinates: list[int] = []
        for size in reversed(sizes):
            rest, coordinate = divmod(rest, size)
            coordinates.append(coordinate)
        coordinates.reverse()
        cell_codes = tuple(codes[d][c] for d, c in enumerate(coordinates))
        cells.append((cell_codes, token, status.get(str(position))))
    return cells
