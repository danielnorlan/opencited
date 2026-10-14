"""Snapshots of query results over the recorded responses (spec 0002, task 1).

The expected cells, bodies and hashes come from the recordings in `tests/fixtures/ssb/`.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from recorded import FIXTURES, raw, recorded_cells

from opencited import snapshots
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.snapshots import (
    BODY,
    MANIFEST,
    OBSERVATIONS,
    SnapshotError,
    SnapshotStore,
    place,
)
from opencited.sources import Language, Observation, QueryRetrieval
from opencited.sources.ssb import BASE_URL, SsbClient

REPLAY = replay_transport(FIXTURES)
NOON = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
SYMBOLS = {
    "RegionSamisk": ["1849", "1850", "1853"],
    "ContentsCode": ["*"],
    "Tid": ["2008", "2009", "2010"],
}
POPULATION = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}
QUERIES: list[tuple[str, Mapping[str, Sequence[str]], Language, str, int]] = [
    ("06856", SYMBOLS, "en", "data-06856-symbols-en", 1800),
    ("06856", SYMBOLS, "no", "data-06856-symbols-no", 1800),
    (
        "06743",
        {
            "FiskeslagSamiske": ["80a"],
            "SamiskeElver": ["18", "18000000127", "18144000000"],
            "ContentsCode": ["Fangst", "Gjennomsnitt"],
            "Tid": ["2015", "2016"],
        },
        "no",
        "data-06743-decimals-no",
        1800,
    ),
    ("05810", POPULATION, "no", "data-05810-no", 1800),
    ("05810", POPULATION, "en", "data-05810-en", 1800),
    (
        "05810",
        {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]},
        "en",
        "data-05810-expressions-en",
        1800,
    ),
    (
        "05810",
        {
            "Kjonn": ["1", "2"],
            "Alder": ["999B"],
            "ContentsCode": ["Personer"],
            "Tid": ["2024", "2025", "2026"],
        },
        "en",
        "data-05810-post-en",
        0,
    ),
]
IDS = [query[3] for query in QUERIES]
PARAMETERS = ("table_id", "selection", "language", "name", "limit")


def retrieve(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language, limit: int
) -> QueryRetrieval:
    async def call() -> QueryRetrieval:
        async with make_client(BASE_URL, transport=REPLAY) as http:
            client = SsbClient(http, max_get_url_length=limit, now=lambda: NOON)
            return await client.retrieve(table_id, selection, language)

    return asyncio.run(call())


def scale(token: str) -> int:
    return len(token.partition(".")[2])


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_a_snapshot_holds_the_recorded_cells_cell_for_cell(
    tmp_path: Path,
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language,
    name: str,
    limit: int,
) -> None:
    store = SnapshotStore(tmp_path)
    retrieval = retrieve(table_id, selection, language, limit)
    snapshot = store.read(store.write(retrieval).name)
    expected = recorded_cells(name)
    assert [cell.codes for cell in snapshot.cells] == [codes for codes, _, _ in expected]
    assert [cell.symbol for cell in snapshot.cells] == [symbol for _, _, symbol in expected]
    assert [cell.value_text for cell in snapshot.cells] == [
        None if symbol is not None else token for _, token, symbol in expected
    ]
    assert [cell.value for cell in snapshot.cells] == [
        None if symbol is not None else Decimal(token) for _, token, symbol in expected
    ]


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_a_snapshot_is_named_by_the_hash_of_its_body(
    tmp_path: Path,
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language,
    name: str,
    limit: int,
) -> None:
    store = SnapshotStore(tmp_path)
    retrieval = retrieve(table_id, selection, language, limit)
    directory = store.write(retrieval)
    body = (directory / BODY).read_bytes()
    assert body == raw(name)
    assert directory.name == hashlib.sha256(body).hexdigest()
    assert directory.name == retrieval.result.provenance.response_sha256
    manifest = store.read(directory.name).manifest
    assert manifest.provenance == retrieval.result.provenance
    assert manifest.language == language
    metadata = raw(f"metadata-{table_id}-{language}")
    assert manifest.metadata_sha256 == hashlib.sha256(metadata).hexdigest()


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_a_snapshot_keeps_the_labels_and_the_units_of_the_metadata(
    tmp_path: Path,
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language,
    name: str,
    limit: int,
) -> None:
    store = SnapshotStore(tmp_path)
    retrieval = retrieve(table_id, selection, language, limit)
    snapshot = store.read(store.write(retrieval).name)
    labels = [
        (dimension.code, dimension.label, [(value.code, value.label) for value in dimension.values])
        for dimension in retrieval.result.dimensions
    ]
    assert [
        (dimension.code, dimension.label, [(value.code, value.label) for value in dimension.values])
        for dimension in snapshot.manifest.dimensions
    ] == labels
    assert snapshot.manifest.units == retrieval.metadata.units
    contents = [
        index for index, dimension in enumerate(retrieval.result.dimensions) if dimension.contents
    ]
    [position] = contents
    for cell in snapshot.cells:
        unit = retrieval.metadata.unit(cell.codes[position])
        assert unit is not None
        assert cell.unit == unit.base


@pytest.mark.parametrize(PARAMETERS, QUERIES, ids=IDS)
def test_the_values_are_decimals_at_the_largest_scale(
    tmp_path: Path,
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language,
    name: str,
    limit: int,
) -> None:
    store = SnapshotStore(tmp_path)
    directory = store.write(retrieve(table_id, selection, language, limit))
    tokens = [token for _, token, symbol in recorded_cells(name) if symbol is None]
    largest = max((scale(token) for token in tokens), default=0)
    with duckdb.connect() as connection:
        columns = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(directory / OBSERVATIONS)]
        ).fetchall()
    types = {str(column[0]): str(column[1]) for column in columns}
    assert types.pop("value") == f"DECIMAL(38,{largest})"
    assert set(types.values()) == {"VARCHAR"}
    assert store.read(directory.name).manifest.scale == largest


def test_writing_again_keeps_the_first_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    first = retrieve("05810", POPULATION, "en", 1800)
    directory = store.write(first)
    files = {path.name: path.read_bytes() for path in directory.iterdir()}
    later = retrieve("05810", POPULATION, "en", 1800)
    assert later.result.provenance.retrieved_at > first.result.provenance.retrieved_at
    assert store.write(later) == directory
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == files
    assert store.read(directory.name).manifest.provenance == first.result.provenance
    assert [path.name for path in tmp_path.iterdir()] == [directory.name]


def test_a_snapshot_whose_body_changed_cannot_be_read(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    directory = store.write(retrieve("05810", POPULATION, "en", 1800))
    body = directory / BODY
    body.write_bytes(body.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="does not hash to its ID"):
        store.read(directory.name)


@pytest.mark.parametrize("field", ["snapshot_id", "provenance"])
def test_a_manifest_that_names_another_snapshot_cannot_be_read(tmp_path: Path, field: str) -> None:
    store = SnapshotStore(tmp_path)
    directory = store.write(retrieve("05810", POPULATION, "en", 1800))
    manifest = store.read(directory.name).manifest
    other = "0" * 64
    if field == "snapshot_id":
        altered = manifest.model_copy(update={"snapshot_id": other})
    else:
        record = manifest.provenance.model_copy(update={"response_sha256": other})
        altered = manifest.model_copy(update={"provenance": record})
    (directory / MANIFEST).write_text(altered.model_dump_json(), encoding="utf-8")
    with pytest.raises(SnapshotError, match="names another snapshot"):
        store.read(directory.name)


def test_changed_cells_cannot_be_read(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    directory = store.write(retrieve("05810", POPULATION, "en", 1800))
    other = store.write(retrieve("05810", POPULATION, "no", 1800))
    # A valid Parquet file, but not the one the manifest records.
    (directory / OBSERVATIONS).write_bytes((other / OBSERVATIONS).read_bytes())
    with pytest.raises(SnapshotError, match="have changed"):
        store.read(directory.name)


def test_a_damaged_manifest_is_reported(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    directory = store.write(retrieve("05810", POPULATION, "en", 1800))
    (directory / MANIFEST).write_text("{}", encoding="utf-8")
    with pytest.raises(SnapshotError, match="cannot be read"):
        store.read(directory.name)


def test_a_manifest_for_another_snapshot_cannot_be_read(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    one = store.write(retrieve("05810", POPULATION, "en", 1800))
    other = store.write(retrieve("05810", POPULATION, "no", 1800))
    (one / MANIFEST).write_bytes((other / MANIFEST).read_bytes())
    with pytest.raises(SnapshotError, match="names another snapshot"):
        store.read(one.name)


@pytest.mark.parametrize("snapshot_id", ["../outside", "A" * 64, "0" * 63, ""])
def test_only_a_sha256_names_a_snapshot(tmp_path: Path, snapshot_id: str) -> None:
    with pytest.raises(SnapshotError, match="is not a snapshot ID"):
        SnapshotStore(tmp_path).read(snapshot_id)


def test_a_missing_snapshot_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="there is no snapshot"):
        SnapshotStore(tmp_path).read("0" * 64)


def with_values(retrieval: QueryRetrieval, *values: Decimal) -> QueryRetrieval:
    """Return the retrieval with its first cells' values replaced, to test a limit.

    These values stand for no response from SSB; the recordings hold none so wide.
    """
    cells = list(retrieval.result.observations)
    for index, value in enumerate(values):
        cells[index] = Observation(codes=cells[index].codes, value=value, symbol=None)
    altered = retrieval.result.model_copy(update={"observations": tuple(cells)})
    return replace(retrieval, result=altered)


@pytest.mark.parametrize(
    ("values", "written"),
    [
        ((Decimal("9" * 38),), True),
        ((Decimal("9" * 39),), False),
        ((Decimal("1E+37"),), True),
        ((Decimal("1E+38"),), False),
        # The .5 sets the scale to 1, so this value needs 38 + 1 digits.
        ((Decimal("9" * 38 + ".5"),), False),
        # A value below 1, and zero, need only the decimals of the scale.
        ((Decimal("0." + "9" * 38), Decimal("0")), True),
        ((Decimal("0." + "0" * 38 + "1"),), False),
        ((Decimal("-" + "9" * 38),), True),
    ],
)
def test_a_value_that_does_not_fit_38_digits_gets_no_snapshot(
    tmp_path: Path, values: tuple[Decimal, ...], written: bool
) -> None:
    store = SnapshotStore(tmp_path)
    retrieval = with_values(retrieve("05810", POPULATION, "en", 1800), *values)
    if written:
        cells = store.read(store.write(retrieval).name).cells
        assert [cell.value for cell in cells[: len(values)]] == list(values)
    else:
        with pytest.raises(SnapshotError, match="does not fit DECIMAL"):
            store.write(retrieval)
        assert list(tmp_path.iterdir()) == []


def test_a_value_is_written_in_fixed_point(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    retrieval = with_values(retrieve("05810", POPULATION, "en", 1800), Decimal("0.0000001"))
    [first, *_] = store.read(store.write(retrieval).name).cells
    assert first.value_text == "0.0000001"
    assert first.value == Decimal("0.0000001")


def test_a_failed_write_leaves_no_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*arguments: object) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(snapshots, "write_cells", broken)
    with pytest.raises(OSError, match="the disk is full"):
        SnapshotStore(tmp_path).write(retrieve("05810", POPULATION, "en", 1800))
    assert list(tmp_path.iterdir()) == []


def test_codes_and_directories_with_quotes_are_written_and_read(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "it's here")
    retrieval = retrieve("05810", POPULATION, "en", 1800)
    first = retrieval.result.dimensions[0].model_copy(update={"code": 'Kj"onn'})
    dimensions = (first, *retrieval.result.dimensions[1:])
    altered = replace(
        retrieval, result=retrieval.result.model_copy(update={"dimensions": dimensions})
    )
    snapshot = store.read(store.write(altered).name)
    assert snapshot.manifest.dimensions[0].code == 'Kj"onn'
    assert [cell.codes for cell in snapshot.cells] == [
        cell.codes for cell in retrieval.result.observations
    ]


def test_the_units_come_from_the_metadata_of_the_query(tmp_path: Path) -> None:
    retrieval = retrieve("05810", POPULATION, "en", 1800)
    # Units that differ from the data response's own, to show which ones the snapshot keeps.
    units = tuple(
        unit.model_copy(update={"base": f"{unit.base} (metadata)"})
        for unit in retrieval.metadata.units
    )
    altered = replace(retrieval, metadata=retrieval.metadata.model_copy(update={"units": units}))
    store = SnapshotStore(tmp_path)
    snapshot = store.read(store.write(altered).name)
    assert snapshot.manifest.units == units
    assert {cell.unit for cell in snapshot.cells} == {unit.base for unit in units}


@pytest.mark.parametrize("code", ["value", "Value", "UNIT", "file_row_number"])
def test_a_dimension_may_not_take_a_column_name(tmp_path: Path, code: str) -> None:
    retrieval = retrieve("05810", POPULATION, "en", 1800)
    first = retrieval.result.dimensions[0].model_copy(update={"code": code})
    dimensions = (first, *retrieval.result.dimensions[1:])
    altered = replace(
        retrieval, result=retrieval.result.model_copy(update={"dimensions": dimensions})
    )
    with pytest.raises(SnapshotError, match="column names"):
        SnapshotStore(tmp_path).write(altered)


def test_a_body_that_does_not_match_the_record_is_refused(tmp_path: Path) -> None:
    retrieval = retrieve("05810", POPULATION, "en", 1800)
    with pytest.raises(SnapshotError, match="does not hash"):
        SnapshotStore(tmp_path).write(replace(retrieval, body=retrieval.body + b" "))
    assert list(tmp_path.iterdir()) == []


def test_a_snapshot_placed_by_another_writer_is_kept(tmp_path: Path) -> None:
    final = tmp_path / ("a" * 64)
    final.mkdir()
    (final / BODY).write_bytes(b"first")
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    (temporary / BODY).write_bytes(b"second")
    assert place(temporary, final) == final
    assert (final / BODY).read_bytes() == b"first"


def test_a_failed_rename_without_a_snapshot_in_place_is_raised(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        place(tmp_path / "missing", tmp_path / ("a" * 64))
