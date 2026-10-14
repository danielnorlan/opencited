"""Write-once snapshots of query results (spec 0002, ADR 0011).

A snapshot keeps what a calculation needs to repeat a result: the raw response body,
the provenance record, the labels of every code, the units from the table's metadata
and the cells as a Parquet file. Its ID is the SHA-256 of the body, which the
provenance record holds as ``response_sha256``.

A snapshot is written to a temporary directory next to its place, flushed to disk and
renamed into place when complete, so a reader sees either no snapshot or a whole one.
The store never changes a snapshot: a later response with the same body finds it in
place, and the snapshot keeps the provenance of the first retrieval. Reading checks
that the body hashes to the ID and that the Parquet file hashes to the SHA-256 that
the manifest records.

The Parquet file has one text column per dimension code, ``value_text`` with the value
in fixed point as SSB writes it, ``value`` as ``DECIMAL(38, s)`` where ``s`` is the
largest scale among the values, ``symbol`` for a missing value and ``unit`` for the
unit of the content. The cells reach DuckDB as JSON strings and are cast to the decimal
type, so no value passes through a binary number. A result with a value that does not
fit ``DECIMAL(38, s)`` gets no snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
from pydantic import Field, ValidationError

from opencited.model import Frozen, Sha256Hex
from opencited.provenance import ProvenanceRecord
from opencited.sources import Language, QueryRetrieval, Unit
from opencited.values import ExactDecimal, exact_text

BODY = "response.json"
MANIFEST = "snapshot.json"
OBSERVATIONS = "observations.parquet"
# The rows on their way to DuckDB, removed before the snapshot is placed.
ROWS = "cells.jsonl"
# The columns after the dimension codes. No dimension code may take one, nor the
# column that DuckDB adds when it reads the row numbers. DuckDB compares column names
# without regard to case.
COLUMNS = ("value_text", "value", "symbol", "unit")
RESERVED = frozenset({*COLUMNS, "file_row_number"})
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
    observations_sha256: Sha256Hex = Field(description="SHA-256 of the Parquet file.")
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
    """Return how many digits ``value`` takes in a decimal type with ``scale`` decimals.

    The type stores the integer part and ``scale`` decimals, so a value below 1, zero
    included, needs ``scale`` digits.
    """
    whole = 0 if value.is_zero() else max(0, value.adjusted() + 1)
    return whole + scale


def scale_of(value: Decimal) -> int:
    return max(0, -cast("int", value.as_tuple().exponent))


def quoted(name: str) -> str:
    """Return an SQL identifier for a column name, with any double quote doubled."""
    return '"' + name.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flushed(path: Path) -> None:
    """Flush a file or a directory to disk, so that a rename never shows empty files."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SnapshotStore:
    """Writes and reads snapshots in one directory, one subdirectory per snapshot."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, snapshot_id: str) -> Path:
        """Return the directory of a snapshot. The ID must be a SHA-256 in hex."""
        if len(snapshot_id) != 64 or any(char not in "0123456789abcdef" for char in snapshot_id):
            raise SnapshotError(f"{snapshot_id!r} is not a snapshot ID")
        return self.directory / snapshot_id

    def check(self) -> None:
        """Create the directory and check that a snapshot can be written there."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            probe = self.directory / f".probe.{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as error:
            raise SnapshotError(
                f"snapshots cannot be written to {self.directory}: {error}"
            ) from error

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
        taken = sorted(code for code in codes if code.lower() in RESERVED)
        if taken:
            raise SnapshotError(f"the dimension codes {taken} are column names of a snapshot")
        values = [cell.value for cell in result.observations if cell.value is not None]
        scale = max((scale_of(value) for value in values), default=0)
        # A value takes at least the scale's digits, so this also refuses a scale above 38.
        if any(digits(value, scale) > MAX_DIGITS for value in values):
            raise SnapshotError(
                f"a value does not fit DECIMAL({MAX_DIGITS}, {scale}), so no snapshot is written"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{snapshot_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            (temporary / BODY).write_bytes(retrieval.body)
            write_cells(temporary, retrieval, codes, scale)
            manifest = Manifest(
                snapshot_id=snapshot_id,
                language=retrieval.language,
                provenance=result.provenance,
                metadata_sha256=retrieval.metadata_sha256,
                observations_sha256=sha256_file(temporary / OBSERVATIONS),
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
            (temporary / MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            for name in (BODY, MANIFEST, OBSERVATIONS):
                flushed(temporary / name)
            flushed(temporary)
            placed = place(temporary, final)
            flushed(self.directory)
            return placed
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def read(self, snapshot_id: str) -> Snapshot:
        """Return a snapshot, after checking its body and its cells against their hashes."""
        directory = self.path(snapshot_id)
        try:
            body = (directory / BODY).read_bytes()
            manifest = Manifest.model_validate_json((directory / MANIFEST).read_bytes())
        except FileNotFoundError as error:
            raise SnapshotError(f"there is no snapshot {snapshot_id}") from error
        except (OSError, ValidationError) as error:
            raise SnapshotError(f"snapshot {snapshot_id} cannot be read: {error}") from error
        if hashlib.sha256(body).hexdigest() != snapshot_id:
            raise SnapshotError(f"the body of snapshot {snapshot_id} does not hash to its ID")
        if (
            manifest.snapshot_id != snapshot_id
            or manifest.provenance.response_sha256 != snapshot_id
        ):
            raise SnapshotError(f"the manifest of snapshot {snapshot_id} names another snapshot")
        observations = directory / OBSERVATIONS
        try:
            if sha256_file(observations) != manifest.observations_sha256:
                raise SnapshotError(f"the cells of snapshot {snapshot_id} have changed")
            cells = read_cells(observations, manifest)
        except (OSError, duckdb.Error, ValidationError) as error:
            raise SnapshotError(f"snapshot {snapshot_id} cannot be read: {error}") from error
        return Snapshot(manifest, cells)


def place(temporary: Path, final: Path) -> Path:
    """Rename a complete snapshot into place, or keep the one that another writer placed."""
    try:
        temporary.rename(final)
    except OSError:
        if not final.exists():
            raise
    return final


def write_cells(directory: Path, retrieval: QueryRetrieval, codes: list[str], scale: int) -> None:
    """Write the cells of a result as Parquet in ``directory``.

    Each row reaches DuckDB as a JSON array of strings, led by its position, and the
    value is cast from text to the decimal type. Binding 5,000 rows one by one took
    seconds; reading them as JSON Lines takes a fraction of one.
    """
    result = retrieval.result
    contents = next(
        (index for index, dimension in enumerate(result.dimensions) if dimension.contents), None
    )
    units = {unit.code: unit.base for unit in retrieval.metadata.units}
    rows = directory / ROWS
    with rows.open("w", encoding="utf-8") as file:
        for position, cell in enumerate(result.observations):
            text = exact_text(cell.value) if cell.value is not None else None
            unit = units.get(cell.codes[contents]) if contents is not None else None
            line = [str(position), *cell.codes, text, cell.symbol, unit]
            file.write(json.dumps(line) + "\n")
    decimal = f"DECIMAL({MAX_DIGITS}, {scale})"
    columns = [f"{quoted(code)} VARCHAR" for code in codes]
    columns += ["value_text VARCHAR", f"value {decimal}", "symbol VARCHAR", "unit VARCHAR"]
    # DuckDB's lists count from 1, and element 1 holds the position.
    fields = [f"row[{index + 2}]" for index in range(len(codes))]
    text = f"row[{len(codes) + 2}]"
    fields += [
        text,
        f"CAST({text} AS {decimal})",
        f"row[{len(codes) + 3}]",
        f"row[{len(codes) + 4}]",
    ]
    with duckdb.connect() as connection:
        connection.execute(f"CREATE TABLE cells ({', '.join(columns)})")
        connection.execute(
            f"INSERT INTO cells SELECT {', '.join(fields)} FROM read_json(?, format = "  # noqa: S608 - list positions and a fixed decimal type only
            "'newline_delimited', records = false, columns = {'row': 'VARCHAR[]'}) "
            "ORDER BY CAST(row[1] AS INTEGER)",
            [str(rows)],
        )
        # COPY takes no parameter for its file; the path is the store's own, quoted.
        target = str(directory / OBSERVATIONS).replace("'", "''")
        connection.execute(f"COPY cells TO '{target}' (FORMAT parquet)")
    rows.unlink()


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
