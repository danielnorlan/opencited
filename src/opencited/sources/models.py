"""Typed results of statistics sources.

Values are ``Decimal`` or missing, never ``float`` (ADR 0009). Every model is frozen and
strict and holds only tuples, so a result cannot change after it is built and no value
is coerced. ``Retrieved`` is an internal dataclass that pairs a result with the exchange
it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import AwareDatetime, Field

from opencited.http import Exchange
from opencited.model import Frozen, TableId
from opencited.provenance import ProvenanceRecord
from opencited.values import ExactDecimal

type Language = Literal["no", "en"]
LANGUAGES: tuple[Language, ...] = ("no", "en")


class TableSummary(Frozen):
    """One table found by a search."""

    table_id: TableId
    title: str
    url: str = Field(description="The table's page in StatBank.")
    first_period: str
    last_period: str
    updated: AwareDatetime
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
    time: bool = Field(description="Whether the dimension holds time periods.")
    contents: bool = Field(
        description="Whether the dimension holds the contents, which have units."
    )
    eliminable: bool = Field(description="Whether a selection may leave the dimension out.")


class Described(Frozen):
    """What metadata and data results share: the table and its dimensions."""

    table_id: TableId
    title: str
    url: str = Field(description="The table's page in StatBank.")
    updated: AwareDatetime
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

    codes: tuple[str, ...] = Field(
        description="One code per dimension, in the order of the dimensions."
    )
    value: ExactDecimal | None = Field(
        description="The value as a decimal string, or null when a symbol replaces it."
    )
    symbol: str | None = Field(
        description="The source's symbol for a missing value, explained in symbols."
    )


class DataResult(Described):
    """The cells of a selection, with the dimensions that the selection covers."""

    observations: tuple[Observation, ...]


class Symbol(Frozen):
    """A symbol that replaces missing values, and the source's explanation when it gives one."""

    symbol: str
    explanation: str | None


class QueryResult(DataResult):
    """The cells of a selection with what a reader needs to check them.

    ``symbols`` lists every symbol that the cells use, with the source's explanation
    where it gives one. ``cached`` is true for a result whose data came from the cache,
    which repeats the provenance of the first retrieval.
    """

    symbols: tuple[Symbol, ...]
    provenance: ProvenanceRecord
    cached: bool


@dataclass(frozen=True)
class QueryRetrieval:
    """A query result with the responses it was built from, which a snapshot keeps.

    ``body`` is the raw body of the data response, whose SHA-256 the provenance record
    holds. ``metadata`` is the table's metadata that the query read, and
    ``metadata_sha256`` the SHA-256 of that response's body. Labels are in
    ``language``, the language of the request.
    """

    result: QueryResult
    body: bytes
    metadata: TableMetadata
    metadata_sha256: str
    language: Language


@dataclass(frozen=True)
class Retrieved[T]:
    """A result and the exchange it came from, which the provenance record describes."""

    value: T
    exchange: Exchange
    cached: bool = False
