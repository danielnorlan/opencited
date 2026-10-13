"""Selections in PxWebApi 2: expanding codes and expressions, and counting cells.

The expressions follow the PxWebApi user guide: masks with ``*`` and ``?``, ``top`` and
``bottom`` with an optional offset, and ``range``, ``from`` and ``to``. For the time
dimension ``top`` selects the latest periods and ``bottom`` the first. For any other
dimension ``top`` selects the first codes in the metadata and ``bottom`` the last.
Recordings of 2026-10-13 confirm masks, ``top`` with and without an offset, ``bottom``,
``range`` and ``from``. Their arguments cannot contain commas or parentheses.

Expanded codes come in the order of the metadata. SSB returns the cells in the order of
the request, so a reply is checked by the codes in each dimension, not their order.
Expressions are sent in a normalised form without spaces, and repeated items once.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from opencited.sources.errors import InvalidSelectionError
from opencited.sources.models import Dimension, TableMetadata

NUMBER = r"\s*([0-9]{1,9})\s*"
ARGUMENT = r"\s*([^,()\s](?:[^,()]*[^,()\s])?)\s*"
TOP = re.compile(rf"top\({NUMBER}(?:,{NUMBER})?\)")
BOTTOM = re.compile(rf"bottom\({NUMBER}(?:,{NUMBER})?\)")
RANGE = re.compile(rf"range\({ARGUMENT},{ARGUMENT}\)")
FROM = re.compile(rf"from\({ARGUMENT}\)")
TO = re.compile(rf"to\({ARGUMENT}\)")
KEYWORDS = ("top(", "bottom(", "range(", "from(", "to(")
FORMS = (
    "top(n), top(n,offset), bottom(n), bottom(n,offset), range(first,last), "
    "from(first), to(last), and masks with * for any characters and ? for one"
)
# An error lists at most this many allowed codes, and says how many more there are.
MAX_LISTED = 20


def glob(pattern: str, text: str) -> bool:
    """Return whether ``text`` matches a mask where ``*`` is any run and ``?`` one character.

    The match walks both strings once, going back only to the last ``*``, so a long mask
    cannot make it slow.
    """
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pattern) and pattern[p] in ("?", text[t]):
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif star >= 0:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def allowed_codes(dimension: Dimension) -> str:
    """Return the codes of a dimension for an error, the latest first for time."""
    codes = [value.code for value in dimension.values]
    if dimension.time:
        shown, rest = codes[-MAX_LISTED:], codes[:-MAX_LISTED]
        listed = ", ".join(shown)
        return f"{len(rest)} earlier periods and {listed}" if rest else listed
    shown, rest = codes[:MAX_LISTED], codes[MAX_LISTED:]
    listed = ", ".join(shown)
    return f"{listed} and {len(rest)} more" if rest else listed


def take(codes: list[str], count: int, offset: int, *, from_end: bool) -> list[str]:
    """Return ``count`` codes after skipping ``offset``, from the start or the end."""
    if from_end:
        end = max(len(codes) - offset, 0)
        return codes[max(end - count, 0) : end]
    return codes[offset : offset + count]


class Expander:
    """Expands the items of one dimension, and collects what it cannot expand."""

    def __init__(self, dimension: Dimension) -> None:
        self.dimension = dimension
        self.codes = [value.code for value in dimension.values]
        self.index = {code: number for number, code in enumerate(self.codes)}
        self.problems: list[str] = []

    def position(self, code: str, item: str) -> int | None:
        found = self.index.get(code)
        if found is None:
            self.problems.append(
                f"{item!r} names the code {code!r}, which {self.dimension.code} lacks"
            )
        return found

    def select(self, item: str) -> list[str]:
        """Return the codes that one code or expression selects, in metadata order."""
        if item in self.index:
            return [item]
        text = item.strip()
        if match := TOP.fullmatch(text):
            return take(self.codes, int(match[1]), int(match[2] or 0), from_end=self.dimension.time)
        if match := BOTTOM.fullmatch(text):
            return take(
                self.codes, int(match[1]), int(match[2] or 0), from_end=not self.dimension.time
            )
        if match := RANGE.fullmatch(text):
            first, last = self.position(match[1], item), self.position(match[2], item)
            if first is None or last is None:
                return []
            if first > last:
                self.problems.append(f"{item!r} ends before it starts in the order of the metadata")
                return []
            return self.codes[first : last + 1]
        if match := FROM.fullmatch(text):
            first = self.position(match[1], item)
            return [] if first is None else self.codes[first:]
        if match := TO.fullmatch(text):
            last = self.position(match[1], item)
            return [] if last is None else self.codes[: last + 1]
        if text.lower().startswith(KEYWORDS):
            self.problems.append(f"{item!r} is not a valid expression; the forms are {FORMS}")
            return []
        if "*" in text or "?" in text:
            return [code for code in self.codes if glob(text, code)]
        self.problems.append(f"{self.dimension.code} has no code {item!r}")
        return []

    def expand(self, items: Sequence[str]) -> tuple[str, ...]:
        """Return the codes that all items select, in metadata order and without repeats.

        An item that selects no code is a problem, so a selection never shrinks quietly.
        """
        chosen: set[str] = set()
        for item in items:
            known = len(self.problems)
            selected = self.select(item)
            if not selected and len(self.problems) == known:
                self.problems.append(f"{item!r} selects no code in {self.dimension.code}")
            chosen.update(selected)
        return tuple(code for code in self.codes if code in chosen)


def expand(dimension: Dimension, items: Sequence[str]) -> tuple[str, ...]:
    """Return the codes that a list of codes and expressions selects in one dimension."""
    expander = Expander(dimension)
    codes = expander.expand(items)
    if expander.problems:
        raise InvalidSelectionError(
            f"{'; '.join(expander.problems)}. The codes of {dimension.code} are "
            f"{allowed_codes(dimension)}, and the table's metadata lists them all."
        )
    return codes


def normalised(item: str) -> str:
    """Return an item as it is sent: an expression without spaces, any other item as is."""
    text = item.strip()
    for pattern, keyword in ((TOP, "top"), (BOTTOM, "bottom"), (RANGE, "range")):
        if match := pattern.fullmatch(text):
            arguments = [argument for argument in match.groups() if argument is not None]
            return f"{keyword}({','.join(arguments)})"
    for pattern, keyword in ((FROM, "from"), (TO, "to")):
        if match := pattern.fullmatch(text):
            return f"{keyword}({match[1]})"
    return item


def normalise(selection: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """Return a selection as it is sent: expressions normalised, repeated items once."""
    return {
        dimension: list(dict.fromkeys(normalised(item) for item in items))
        for dimension, items in selection.items()
    }


def resolve(
    metadata: TableMetadata, selection: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, ...]]:
    """Return the codes that a selection covers in each dimension it names.

    A dimension that the selection leaves out must be one that can be eliminated, and
    then counts as one cell. Every problem in the selection is reported in one error.
    """
    dimensions = {dimension.code: dimension for dimension in metadata.dimensions}
    problems: list[str] = []
    unknown = [code for code in selection if code not in dimensions]
    if unknown:
        problems.append(
            f"Table {metadata.table_id} has no dimension {', '.join(unknown)}; "
            f"its dimensions are {', '.join(dimensions)}"
        )
    missing = [
        dimension.code
        for dimension in metadata.dimensions
        if dimension.code not in selection and not dimension.eliminable
    ]
    if missing:
        problems.append(
            f"The selection needs codes for {', '.join(missing)}, which cannot be left out"
        )
    resolved: dict[str, tuple[str, ...]] = {}
    for dimension in metadata.dimensions:
        if dimension.code in selection:
            expander = Expander(dimension)
            resolved[dimension.code] = expander.expand(selection[dimension.code])
            if expander.problems:
                problems.append(
                    f"{'; '.join(expander.problems)}. The codes of {dimension.code} are "
                    f"{allowed_codes(dimension)}"
                )
    if problems:
        raise InvalidSelectionError(
            ". ".join(problems) + ". The table's metadata lists every dimension and code."
        )
    return resolved


def cell_count(resolved: Mapping[str, Sequence[str]]) -> int:
    """Return the number of cells that resolved codes cover. Left-out dimensions count once."""
    return math.prod(len(codes) for codes in resolved.values())
