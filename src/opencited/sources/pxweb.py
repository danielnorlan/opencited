"""Selections in PxWebApi 2: expanding codes and expressions, and counting cells.

The expressions follow the PxWebApi user guide: masks with ``*`` and ``?``, ``top`` and
``bottom`` with an optional offset, and ``range``, ``from`` and ``to``. For the time
dimension ``top`` selects the latest periods and ``bottom`` the first. For any other
dimension ``top`` selects the first codes in the metadata and ``bottom`` the last.
Expanded codes keep the order of the metadata, which is the order of the cells.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from opencited.sources.errors import InvalidSelectionError
from opencited.sources.models import Dimension, TableMetadata

TOP = re.compile(r"top\(([0-9]+)(?:,\s*([0-9]+))?\)")
BOTTOM = re.compile(r"bottom\(([0-9]+)(?:,\s*([0-9]+))?\)")
RANGE = re.compile(r"range\(([^,()]+),\s*([^,()]+)\)")
FROM = re.compile(r"from\(([^,()]+)\)")
TO = re.compile(r"to\(([^,()]+)\)")
# An error lists at most this many allowed values, and says how many more there are.
MAX_LISTED = 20


def listing(values: Sequence[str]) -> str:
    """Return values for an error message, shortened when there are many."""
    shown = ", ".join(values[:MAX_LISTED])
    rest = len(values) - MAX_LISTED
    return f"{shown} and {rest} more" if rest > 0 else shown


def unknown_code(dimension: Dimension, code: str) -> InvalidSelectionError:
    codes = [value.code for value in dimension.values]
    return InvalidSelectionError(
        f"{dimension.code} has no code {code!r}. Its codes are {listing(codes)}."
    )


def position(dimension: Dimension, codes: list[str], code: str) -> int:
    try:
        return codes.index(code.strip())
    except ValueError:
        raise unknown_code(dimension, code.strip()) from None


def take(codes: list[str], count: int, offset: int, *, from_end: bool) -> list[str]:
    """Return ``count`` codes after skipping ``offset``, from the start or the end."""
    if from_end:
        end = len(codes) - offset
        return codes[max(end - count, 0) : max(end, 0)]
    return codes[offset : offset + count]


def mask(pattern: str) -> re.Pattern[str]:
    """Return a pattern for a mask, where ``*`` is any run of characters and ``?`` one."""
    parts = (".*" if char == "*" else "." if char == "?" else re.escape(char) for char in pattern)
    return re.compile("".join(parts))


def select(dimension: Dimension, item: str) -> list[str]:
    """Return the codes that one code or expression selects, in the order of the metadata."""
    codes = [value.code for value in dimension.values]
    if match := TOP.fullmatch(item):
        return take(codes, int(match[1]), int(match[2] or 0), from_end=dimension.time)
    if match := BOTTOM.fullmatch(item):
        return take(codes, int(match[1]), int(match[2] or 0), from_end=not dimension.time)
    if match := RANGE.fullmatch(item):
        first = position(dimension, codes, match[1])
        last = position(dimension, codes, match[2])
        if first > last:
            raise InvalidSelectionError(
                f"{item} in {dimension.code} ends before it starts in the order of the metadata"
            )
        return codes[first : last + 1]
    if match := FROM.fullmatch(item):
        return codes[position(dimension, codes, match[1]) :]
    if match := TO.fullmatch(item):
        return codes[: position(dimension, codes, match[1]) + 1]
    if "*" in item or "?" in item:
        pattern = mask(item)
        return [code for code in codes if pattern.fullmatch(code)]
    if item in codes:
        return [item]
    raise unknown_code(dimension, item)


def expand(dimension: Dimension, items: Sequence[str]) -> tuple[str, ...]:
    """Return the codes that a list of codes and expressions selects, without repeats."""
    chosen: set[str] = set()
    for item in items:
        chosen.update(select(dimension, item))
    return tuple(value.code for value in dimension.values if value.code in chosen)


def resolve(
    metadata: TableMetadata, selection: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, ...]]:
    """Return the codes that a selection covers in each dimension it names.

    A dimension that the selection leaves out must be one that can be eliminated, and
    then counts as one cell. Every named dimension must select at least one code.
    """
    dimensions = {dimension.code: dimension for dimension in metadata.dimensions}
    unknown = [code for code in selection if code not in dimensions]
    if unknown:
        raise InvalidSelectionError(
            f"Table {metadata.table_id} has no dimension {listing(unknown)}. "
            f"Its dimensions are {listing(list(dimensions))}."
        )
    missing = [
        dimension.code
        for dimension in metadata.dimensions
        if dimension.code not in selection and not dimension.eliminable
    ]
    if missing:
        raise InvalidSelectionError(
            f"The selection needs codes for {listing(missing)}, which cannot be left out."
        )
    resolved: dict[str, tuple[str, ...]] = {}
    for dimension in metadata.dimensions:
        if dimension.code in selection:
            codes = expand(dimension, selection[dimension.code])
            if not codes:
                raise InvalidSelectionError(f"The selection for {dimension.code} selects no code.")
            resolved[dimension.code] = codes
    return resolved


def cell_count(resolved: Mapping[str, Sequence[str]]) -> int:
    """Return the number of cells that resolved codes cover. Left-out dimensions count once."""
    return math.prod(len(codes) for codes in resolved.values())
