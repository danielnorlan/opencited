"""Snapshots for the calculation tests, made from recorded responses.

`recorded_snapshot` stores a snapshot of a recorded query. `altered_snapshot` stores
one with other values, units or codes, for limits that no recording reaches; such a
snapshot stands for no response from SSB, and its body says so.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from recorded import FIXTURES

from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources import Dimension, Language, Observation, QueryRetrieval, Unit, Value
from opencited.sources.ssb import BASE_URL, SsbClient

REPLAY = replay_transport(FIXTURES)
NOON = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
# Recorded queries: 63 cells of table 05810 in English, and 12 of table 06743 in
# Norwegian, with decimals and two contents.
POPULATION = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}
CATCH = {
    "FiskeslagSamiske": ["80a"],
    "SamiskeElver": ["18", "18000000127", "18144000000"],
    "ContentsCode": ["Fangst", "Gjennomsnitt"],
    "Tid": ["2015", "2016"],
}
SYMBOLS = {
    "RegionSamisk": ["1849", "1850", "1853"],
    "ContentsCode": ["*"],
    "Tid": ["2008", "2009", "2010"],
}


def retrieve(
    table_id: str, selection: Mapping[str, Sequence[str]], language: Language
) -> QueryRetrieval:
    async def call() -> QueryRetrieval:
        async with make_client(BASE_URL, transport=REPLAY) as http:
            client = SsbClient(http, now=lambda: NOON)
            return await client.retrieve(table_id, selection, language)

    return asyncio.run(call())


def population() -> QueryRetrieval:
    return retrieve("05810", POPULATION, "en")


def catch() -> QueryRetrieval:
    return retrieve("06743", CATCH, "no")


def symbols() -> QueryRetrieval:
    return retrieve("06856", SYMBOLS, "en")


def recorded_snapshot(store: SnapshotStore, retrieval: QueryRetrieval) -> str:
    return store.write(retrieval).name


def altered(
    retrieval: QueryRetrieval,
    *,
    values: Sequence[Decimal | None] | None = None,
    units: Sequence[Unit] | None = None,
    dimensions: Sequence[Dimension] | None = None,
    cells: Sequence[Observation] | None = None,
) -> QueryRetrieval:
    """Return a retrieval with other values, units or cells, and a body of its own."""
    result = retrieval.result
    observations = list(cells if cells is not None else result.observations)
    if values is not None:
        observations = [
            Observation(codes=cell.codes, value=value, symbol=None if value is not None else "..")
            for cell, value in zip(observations, values, strict=True)
        ]
    update: dict[str, object] = {"observations": tuple(observations)}
    if dimensions is not None:
        update["dimensions"] = tuple(dimensions)
    changed = result.model_copy(update=update)
    body = b"Altered for a test, not a response from SSB: " + changed.model_dump_json().encode()
    record = changed.provenance.model_copy(
        update={"response_sha256": hashlib.sha256(body).hexdigest()}
    )
    metadata = retrieval.metadata
    if units is not None:
        metadata = metadata.model_copy(update={"units": tuple(units)})
    return replace(
        retrieval,
        result=changed.model_copy(update={"provenance": record}),
        body=body,
        metadata=metadata,
    )


def altered_snapshot(store: SnapshotStore, retrieval: QueryRetrieval) -> str:
    return store.write(retrieval).name


def wide(codes: int) -> QueryRetrieval:
    """Return a retrieval of table 05810 whose age dimension has ``codes`` codes.

    It stands for no response from SSB, for limits on the number of cells.
    """
    base = population()
    dimensions = list(base.result.dimensions)
    ages = dimensions[1]
    dimensions[1] = ages.model_copy(
        update={
            "values": tuple(
                Value(code=f"A{index}", label=f"Age {index}", notes=()) for index in range(codes)
            )
        }
    )
    cells = [
        Observation(
            codes=(sex.code, f"A{index}", "Personer", year.code), value=Decimal(index), symbol=None
        )
        for sex in dimensions[0].values
        for index in range(codes)
        for year in dimensions[3].values
    ]
    return altered(base, dimensions=dimensions, cells=cells)
