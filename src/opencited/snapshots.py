"""Write-once snapshots of query results (spec 0002, ADR 0011).

A snapshot keeps what a calculation needs to repeat a result: the raw response body,
the provenance record, the labels of every code, the units from the table's metadata
and the cells as a Parquet file. Its ID is the SHA-256 of the body, which the
provenance record holds as ``response_sha256``.

A snapshot is written to a temporary directory next to its place and renamed into it
when complete, so a reader sees either no snapshot or a whole one. A snapshot is
never changed: a later response with the same body finds it in place and keeps the
provenance of the first retrieval.

The Parquet file has one text column per dimension code, ``value_text`` with the value
as SSB wrote it, ``value`` as ``DECIMAL(38, s)`` where ``s`` is the largest scale among
the values, ``symbol`` for a missing value and ``unit`` for the unit of the content. A
result with a value that does not fit ``DECIMAL(38, s)`` gets no snapshot.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
from pydantic import Field

from opencited.model import Frozen, Sha256Hex
from opencited.provenance import ProvenanceRecord
from opencited.sources import Language, QueryRetrieval, Unit
from opencited.values import ExactDecimal, exact_text

BODY = "response.json"
MANIFEST = "snapshot.json"
OBSERVATIONS = "observations.parquet"
# The columns after the dimension codes, which no dimension code may take.
COLUMNS = ("value_text", "value", "symbol", "unit")
# DuckDB's widest decimal type holds 38 digits.
MAX_DIGITS = 38


class SnapshotError(Exception):
    """A snapshot cannot be written or read."""


class Label(Frozen):
    code: str
    label: str


class DimensionLabels(Frozen):
    """A dimension of the result, with the label of every code in it."""

    code: str
    label: str
    values: tuple[Label, ...]


class Manifest(Frozen):
    """What a snapshot holds besides the body and the cells."""

    snapshot_id: Sha256Hex
    language: Language
    provenance: ProvenanceRecord
    metadata_sha256: Sha256Hex = Field(description="SHA-256 of the metadata response read.")
    dimensions: tuple[DimensionLabels, ...]
    units: tuple[Unit, ...]
    scale: int = Field(ge=0, le=MAX_DIGITS, description="The scale of the value column.")


class Cell(Frozen):
    """One cell of a snapshot, as the Parquet file holds it."""

    codes: tuple[str, ...]
    value_text: str | None
    value: ExactDecimal | None
    symbol: str | None
    unit: str | None


@dataclass(frozen=True)
class Snapshot:
    manifest: Manifest
    cells: tuple[Cell, ...]


def digits(value: Decimal, scale: int) -> int:
    """Return how many digits ``value`` takes when written with ``scale`` decimals."""
    _, coefficient, exponent = value.as_tuple()
    return len(coefficient) + cast("int", exponent) + scale


def scale_of(value: Decimal) -> int:
    return max(0, -cast("int", value.as_tuple().exponent))


def quoted(name: str) -> str:
    """Return an SQL identifier for a column name, with any double quote doubled."""
    return '"' + name.replace('"', '""') + '"'


class SnapshotStore:
    """Writes and reads snapshots in one directory, one subdirectory per snapshot."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, snapshot_id: str) -> Path:
        """Return the directory of a snapshot. The ID must be a SHA-256 in hex."""
        if len(snapshot_id) != 64 or any(char not in "0123456789abcdef" for char in snapshot_id):
            raise SnapshotError(f"{snapshot_id!r} is not a snapshot ID")
        return self.directory / snapshot_id

    def write(self, retrieval: QueryRetrieval) -> Path:
        """Write the snapshot of a query result, unless it exists, and return its directory."""
        result = retrieval.result
        snapshot_id = result.provenance.response_sha256
        if hashlib.sha256(retrieval.body).hexdigest() != snapshot_id:
            raise SnapshotError("the body does not hash to the provenance record's SHA-256")
        final = self.path(snapshot_id)
        if final.exists():
            return final
        codes = [dimension.code for dimension in result.dimensions]
        taken = sorted(set(codes) & set(COLUMNS))
        if taken:
            raise SnapshotError(f"the dimension codes {taken} are column names of a snapshot")
        values = [cell.value for cell in result.observations if cell.value is not None]
        scale = max((scale_of(value) for value in values), default=0)
        wide = [value for value in values if digits(value, scale) > MAX_DIGITS]
        if scale > MAX_DIGITS or wide:
            raise SnapshotError(
                f"a value does not fit DECIMAL({MAX_DIGITS}, {scale}), so no snapshot is written"
            )
        manifest = Manifest(
            snapshot_id=snapshot_id,
            language=retrieval.language,
            provenance=result.provenance,
            metadata_sha256=retrieval.metadata_sha256,
            dimensions=tuple(
                DimensionLabels(
                    code=dimension.code,
                    label=dimension.label,
                    values=tuple(
                        Label(code=value.code, label=value.label) for value in dimension.values
                    ),
                )
                for dimension in result.dimensions
            ),
            units=retrieval.metadata.units,
            scale=scale,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{snapshot_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            (temporary / BODY).write_bytes(retrieval.body)
            (temporary / MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            write_cells(temporary / OBSERVATIONS, retrieval, codes, scale)
            return place(temporary, final)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def read(self, snapshot_id: str) -> Snapshot:
        """Return a snapshot, after checking that its body hashes to its ID."""
        directory = self.path(snapshot_id)
        try:
            body = (directory / BODY).read_bytes()
            manifest = Manifest.model_validate_json((directory / MANIFEST).read_bytes())
        except FileNotFoundError as error:
            raise SnapshotError(f"there is no snapshot {snapshot_id}") from error
        if hashlib.sha256(body).hexdigest() != snapshot_id:
            raise SnapshotError(f"the body of snapshot {snapshot_id} does not hash to its ID")
        if (
            manifest.snapshot_id != snapshot_id
            or manifest.provenance.response_sha256 != snapshot_id
        ):
            raise SnapshotError(f"the manifest of snapshot {snapshot_id} names another snapshot")
        return Snapshot(manifest, read_cells(directory / OBSERVATIONS, manifest))


def place(temporary: Path, final: Path) -> Path:
    """Rename a complete snapshot into place, or keep the one that another writer placed."""
    try:
        temporary.rename(final)
    except OSError:
        if not final.exists():
            raise
    return final


def write_cells(path: Path, retrieval: QueryRetrieval, codes: list[str], scale: int) -> None:
    """Write the cells as Parquet, with each value bound as text and cast to the decimal type."""
    result = retrieval.result
    contents = next(
        (index for index, dimension in enumerate(result.dimensions) if dimension.contents), None
    )
    units = {unit.code: unit.base for unit in retrieval.metadata.units}
    decimal = f"DECIMAL({MAX_DIGITS}, {scale})"
    columns = [f"{quoted(code)} VARCHAR" for code in codes]
    columns += ["value_text VARCHAR", f"value {decimal}", "symbol VARCHAR", "unit VARCHAR"]
    rows: list[list[str | None]] = []
    for cell in result.observations:
        text = exact_text(cell.value) if cell.value is not None else None
        unit = units.get(cell.codes[contents]) if contents is not None else None
        rows.append([*cell.codes, text, text, cell.symbol, unit])
    marks = ", ".join(["?"] * len(codes) + ["?", f"CAST(? AS {decimal})", "?", "?"])
    with duckdb.connect() as connection:
        connection.execute(f"CREATE TABLE cells ({', '.join(columns)})")
        if rows:
            insert = f"INSERT INTO cells VALUES ({marks})"  # noqa: S608 - only placeholders and a fixed decimal type
            connection.executemany(insert, rows)
        # COPY takes no parameter for its file; the path is the store's own, quoted.
        target = str(path).replace("'", "''")
        connection.execute(f"COPY cells TO '{target}' (FORMAT parquet)")


def read_cells(path: Path, manifest: Manifest) -> tuple[Cell, ...]:
    codes = [dimension.code for dimension in manifest.dimensions]
    names = ", ".join(quoted(name) for name in [*codes, *COLUMNS])
    with duckdb.connect() as connection:
        rows = connection.execute(
            f"SELECT {names} FROM read_parquet(?, file_row_number = true) ORDER BY file_row_number",  # noqa: S608 - quoted column names only
            [str(path)],
        ).fetchall()
    count = len(codes)
    return tuple(
        Cell(
            codes=tuple(cast("list[str]", list(row[:count]))),
            value_text=cast("str | None", row[count]),
            value=cast("Decimal | None", row[count + 1]),
            symbol=cast("str | None", row[count + 2]),
            unit=cast("str | None", row[count + 3]),
        )
        for row in rows
    )
