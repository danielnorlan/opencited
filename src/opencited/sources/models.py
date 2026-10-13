"""Typed results of statistics sources.

Values are ``Decimal`` or missing, never ``float`` (ADR 0009). Every model is frozen and
strict and holds only tuples, so a result cannot change after it is built and no value
is coerced. ``Retrieved`` is an internal dataclass that pairs a result with the exchange
it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from opencited.http import Exchange

type Language = Literal["no", "en"]
LANGUAGES: tuple[Language, ...] = ("no", "en")
type TableId = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")]


class Frozen(BaseModel):
    """A strict model that cannot change after it is built."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class TableSummary(Frozen):
    """One table found by a search."""

    table_id: TableId
    title: str
    first_period: str
    last_period: str
    updated: datetime
    time_unit: str | None
    variables: tuple[str, ...]


class SearchResult(Frozen):
    """The tables that a search returned, and how many tables matched in all."""

    total: int
    tables: tuple[TableSummary, ...]


class Value(Frozen):
    """A code of a dimension, its label in the language of the request, and its notes."""

    code: str
    label: str
    notes: tuple[str, ...]


class Unit(Frozen):
    """The unit of one content, and the number of decimals that the source publishes."""

    code: str
    base: str
    decimals: int


class Dimension(Frozen):
    """A dimension of a table, with its values in the order of the source."""

    code: str
    label: str
    values: tuple[Value, ...]
    notes: tuple[str, ...]
    time: bool
    contents: bool
    eliminable: bool


class Described(Frozen):
    """What metadata and data results share: the table and its dimensions."""

    table_id: TableId
    title: str
    updated: datetime
    source: str
    dimensions: tuple[Dimension, ...]
    units: tuple[Unit, ...]
    notes: tuple[str, ...]

    def unit(self, code: str) -> Unit | None:
        """Return the unit of a content code, or None when the source gives none."""
        return next((unit for unit in self.units if unit.code == code), None)


class TableMetadata(Described):
    """The description of a table: its dimensions, units and notes."""


class Observation(Frozen):
    """One cell: a code for each dimension, and a value or the symbol that replaces it."""

    codes: tuple[str, ...]
    value: Decimal | None
    symbol: str | None


class DataResult(Described):
    """The cells of a selection, with the dimensions that the selection covers."""

    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class Retrieved[T]:
    """A result and the exchange it came from, which the provenance record describes."""

    value: T
    exchange: Exchange
