"""Typed results of statistics sources.

Values are ``Decimal`` or missing, never ``float`` (ADR 0009). Every model is frozen and
strict, so a result cannot change after it is built and no value is coerced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from opencited.http import Exchange

type Language = Literal["no", "en"]


class Frozen(BaseModel):
    """A strict model that cannot change after it is built."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class TableSummary(Frozen):
    """One table found by a search."""

    table_id: str
    title: str
    first_period: str
    last_period: str
    updated: datetime
    time_unit: str | None
    variables: tuple[str, ...]


class Value(Frozen):
    """A code of a dimension and its label in the language of the request."""

    code: str
    label: str


class Unit(Frozen):
    """The unit of a content, and the number of decimals that the source publishes."""

    base: str
    decimals: int


class Dimension(Frozen):
    """A dimension of a table, with its values in the order of the source."""

    code: str
    label: str
    values: tuple[Value, ...]
    time: bool
    contents: bool
    eliminable: bool


class TableMetadata(Frozen):
    """The description of a table: its dimensions, units and notes."""

    table_id: str
    title: str
    updated: datetime
    source: str
    dimensions: tuple[Dimension, ...]
    units: dict[str, Unit]
    notes: tuple[str, ...]


class Observation(Frozen):
    """One cell: a code for each dimension, and a value or the symbol that replaces it."""

    codes: tuple[str, ...]
    value: Decimal | None
    symbol: str | None


class DataResult(Frozen):
    """The cells of a selection, with the dimensions that the selection covers."""

    table_id: str
    title: str
    updated: datetime
    source: str
    dimensions: tuple[Dimension, ...]
    units: dict[str, Unit]
    observations: tuple[Observation, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Retrieved[T]:
    """A result and the exchange it came from, which the provenance record describes."""

    value: T
    exchange: Exchange
