"""Statistics Norway's StatBank through PxWebApi 2 (ADR 0007).

The client searches tables, reads their metadata and fetches data. Responses are parsed
from their raw bytes with decimals (ADR 0009), and every result is a frozen model. The
client checks its input before it sends anything, so an invalid table ID or limit never
reaches SSB.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import cast

import httpx2

from opencited.http import Exchange, send
from opencited.sources.errors import (
    InvalidResponseError,
    RateLimitedError,
    RejectedRequestError,
    SourceUnavailableError,
    TableNotFoundError,
)
from opencited.sources.models import (
    DataResult,
    Dimension,
    Frozen,
    Language,
    Observation,
    Retrieved,
    TableMetadata,
    TableSummary,
    Unit,
    Value,
)
from opencited.values import load_json

BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
# SSB answers a GET URL longer than about 2,100 characters with an HTML error page, so
# longer selections go as POST (ADR 0007).
MAX_GET_URL_LENGTH = 1800
TABLE_ID = re.compile(r"\d{5}")
MAX_SEARCH_LIMIT = 50

type JsonObject = dict[str, object]
type Params = tuple[tuple[str, str], ...]


class ApiConfig(Frozen):
    """What `/config` reports about the API."""

    api_version: str
    app_version: str
    max_data_cells: int
    languages: tuple[str, ...]
    licence: str


def check_table_id(table_id: str) -> None:
    """Reject a table ID that is not five digits, before any request is sent."""
    if not TABLE_ID.fullmatch(table_id):
        raise ValueError(f"a table ID has five digits, not {table_id!r}")


def get_codes(codes: Sequence[str]) -> str:
    """Join the codes of one dimension for a GET URL.

    A comma separates codes, so an expression that contains a comma, such as
    ``range(2020,2023)``, is wrapped in brackets.
    """
    return ",".join(f"[{code}]" if "," in code else code for code in codes)


def data_request(
    client: httpx2.AsyncClient,
    table_id: str,
    selection: Mapping[str, Sequence[str]],
    language: Language,
    max_get_url_length: int = MAX_GET_URL_LENGTH,
) -> httpx2.Request:
    """Return the data request for a selection: GET while the URL is short, POST otherwise."""
    path = f"/tables/{table_id}/data"
    params: Params = (
        ("lang", language),
        *((f"valueCodes[{dimension}]", get_codes(codes)) for dimension, codes in selection.items()),
        ("outputFormat", "json-stat2"),
    )
    request = client.build_request("GET", path, params=params)
    if len(str(request.url)) < max_get_url_length:
        return request
    body = {
        "selection": [
            {"variableCode": dimension, "valueCodes": list(codes)}
            for dimension, codes in selection.items()
        ]
    }
    return client.build_request(
        "POST", path, params=(("lang", language), ("outputFormat", "json-stat2")), json=body
    )


class SsbClient:
    """A client for StatBank. It shares the HTTP client that it is given."""

    def __init__(
        self, http: httpx2.AsyncClient, *, max_get_url_length: int = MAX_GET_URL_LENGTH
    ) -> None:
        self._http = http
        self._max_get_url_length = max_get_url_length

    async def config(self) -> ApiConfig:
        """Return what the API reports about itself."""
        document, _ = await self._fetch(self._http.build_request("GET", "/config"))
        return parse_config(document)

    async def search(
        self, query: str, language: Language = "no", limit: int = 10
    ) -> tuple[TableSummary, ...]:
        """Return the tables that match a free-text query, in SSB's order."""
        if not query.strip():
            raise ValueError("the query is empty")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"the limit is from 1 to {MAX_SEARCH_LIMIT}, not {limit}")
        params: Params = (("query", query), ("lang", language), ("pageSize", str(limit)))
        document, _ = await self._fetch(self._http.build_request("GET", "/tables", params=params))
        return parse_search(document)

    async def metadata(self, table_id: str, language: Language = "no") -> TableMetadata:
        """Return the dimensions, units and notes of a table."""
        check_table_id(table_id)
        request = self._http.build_request(
            "GET", f"/tables/{table_id}/metadata", params=(("lang", language),)
        )
        document, _ = await self._fetch(request, table_id)
        return parse_metadata(document, table_id)

    async def data(
        self,
        table_id: str,
        selection: Mapping[str, Sequence[str]],
        language: Language = "no",
    ) -> Retrieved[DataResult]:
        """Return the cells of a selection, which maps dimension codes to value codes."""
        check_table_id(table_id)
        if not selection or any(not codes for codes in selection.values()):
            raise ValueError("every dimension in a selection needs at least one code")
        request = data_request(self._http, table_id, selection, language, self._max_get_url_length)
        document, exchange = await self._fetch(request, table_id)
        return Retrieved(parse_data(document, table_id), exchange)

    async def _fetch(
        self, request: httpx2.Request, table_id: str | None = None
    ) -> tuple[object, Exchange]:
        """Send a request and return its JSON body, or raise the error that fits."""
        try:
            exchange = await send(self._http, request)
        except httpx2.TimeoutException as error:
            raise SourceUnavailableError("SSB did not answer in time") from error
        except httpx2.TransportError as error:
            raise SourceUnavailableError("SSB could not be reached") from error
        if "json" not in exchange.content_type:
            raise SourceUnavailableError(
                f"SSB answered with HTTP {exchange.status} and no JSON body"
            )
        try:
            document = load_json(exchange.content)
        except ValueError as error:
            raise InvalidResponseError("SSB's answer is not valid JSON") from error
        if exchange.status == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimitedError("SSB's rate limit was reached")
        if exchange.status == HTTPStatus.NOT_FOUND and table_id is not None:
            raise TableNotFoundError(f"SSB has no table {table_id}")
        if exchange.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise SourceUnavailableError(f"SSB answered with HTTP {exchange.status}")
        if exchange.status >= HTTPStatus.BAD_REQUEST:
            raise RejectedRequestError(f"SSB rejected the request: {problem_title(document)}")
        return document, exchange


# Parsing. JSON from `load_json` holds every number as a Decimal, so whole numbers that
# describe structure, such as sizes and positions, are converted here and checked.


def obj(value: object, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise InvalidResponseError(f"{what} is not an object")
    return cast("JsonObject", value)


def arr(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidResponseError(f"{what} is not a list")
    return cast("list[object]", value)


def text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise InvalidResponseError(f"{what} is not a string")
    return value


def whole(value: object, what: str) -> int:
    if not isinstance(value, Decimal) or value != value.to_integral_value():
        raise InvalidResponseError(f"{what} is not a whole number")
    return int(value)


def timestamp(value: object, what: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text(value, what))
    except ValueError as error:
        raise InvalidResponseError(f"{what} is not a timestamp") from error
    if moment.tzinfo is None:
        raise InvalidResponseError(f"{what} has no time zone")
    return moment


def problem_title(document: object) -> str:
    """Return the title of an error document, or a plain phrase when it has none."""
    if isinstance(document, dict):
        title = cast("JsonObject", document).get("title")
        if isinstance(title, str):
            return title
    return "no reason given"


def parse_config(document: object) -> ApiConfig:
    config = obj(document, "the configuration")
    languages = arr(config.get("languages"), "the languages")
    return ApiConfig(
        api_version=text(config.get("apiVersion"), "apiVersion"),
        app_version=text(config.get("appVersion"), "appVersion"),
        max_data_cells=whole(config.get("maxDataCells"), "maxDataCells"),
        languages=tuple(
            text(obj(item, "a language").get("id"), "a language ID") for item in languages
        ),
        licence=text(config.get("license"), "license"),
    )


def parse_search(document: object) -> tuple[TableSummary, ...]:
    tables = arr(obj(document, "the search result").get("tables"), "tables")
    summaries: list[TableSummary] = []
    for item in tables:
        table = obj(item, "a table")
        time_unit = table.get("timeUnit")
        summaries.append(
            TableSummary(
                table_id=text(table.get("id"), "a table ID"),
                title=text(table.get("label"), "a table label"),
                first_period=text(table.get("firstPeriod"), "firstPeriod"),
                last_period=text(table.get("lastPeriod"), "lastPeriod"),
                updated=timestamp(table.get("updated"), "updated"),
                time_unit=time_unit if isinstance(time_unit, str) else None,
                variables=tuple(
                    text(name, "a variable name")
                    for name in arr(table.get("variableNames", []), "variableNames")
                ),
            )
        )
    return tuple(summaries)


def ordered_codes(index: object, what: str) -> list[str]:
    """Return the codes of a JSON-stat category in their order.

    SSB sends the index as a mapping from code to position, and the positions must run
    from zero without gaps.
    """
    positions = obj(index, what)
    order = sorted((whole(position, what), code) for code, position in positions.items())
    if [position for position, _ in order] != list(range(len(order))):
        raise InvalidResponseError(f"{what} has gaps in its positions")
    return [code for _, code in order]


def parse_dimensions(dataset: JsonObject) -> tuple[Dimension, ...]:
    role = obj(dataset.get("role", {}), "role")
    time = {text(code, "a time dimension") for code in arr(role.get("time", []), "role.time")}
    metric = {
        text(code, "a metric dimension") for code in arr(role.get("metric", []), "role.metric")
    }
    described = obj(dataset.get("dimension"), "dimension")
    dimensions: list[Dimension] = []
    for code in arr(dataset.get("id"), "id"):
        dimension_id = text(code, "a dimension ID")
        dimension = obj(described.get(dimension_id), f"dimension {dimension_id}")
        category = obj(dimension.get("category"), f"the category of {dimension_id}")
        labels = obj(category.get("label", {}), f"the labels of {dimension_id}")
        extension = obj(dimension.get("extension", {}), f"the extension of {dimension_id}")
        dimensions.append(
            Dimension(
                code=dimension_id,
                label=text(dimension.get("label"), f"the label of {dimension_id}"),
                values=tuple(
                    Value(
                        code=value,
                        label=text(labels.get(value, value), f"a label of {dimension_id}"),
                    )
                    for value in ordered_codes(
                        category.get("index"), f"the index of {dimension_id}"
                    )
                ),
                time=dimension_id in time,
                contents=dimension_id in metric,
                eliminable=extension.get("elimination") is True,
            )
        )
    return tuple(dimensions)


def parse_units(dataset: JsonObject, dimensions: tuple[Dimension, ...]) -> dict[str, Unit]:
    described = obj(dataset.get("dimension"), "dimension")
    units: dict[str, Unit] = {}
    for dimension in dimensions:
        if not dimension.contents:
            continue
        category = obj(
            obj(described.get(dimension.code), dimension.code).get("category"), "category"
        )
        for code, unit in obj(category.get("unit", {}), f"the units of {dimension.code}").items():
            described_unit = obj(unit, f"the unit of {code}")
            units[code] = Unit(
                base=text(described_unit.get("base"), f"the unit base of {code}"),
                decimals=whole(described_unit.get("decimals"), f"the decimals of {code}"),
            )
    return units


def parse_notes(dataset: JsonObject) -> tuple[str, ...]:
    return tuple(text(note, "a note") for note in arr(dataset.get("note", []), "note"))


def parse_metadata(document: object, table_id: str) -> TableMetadata:
    dataset = obj(document, "the metadata")
    dimensions = parse_dimensions(dataset)
    return TableMetadata(
        table_id=table_id,
        title=text(dataset.get("label"), "the table label"),
        updated=timestamp(dataset.get("updated"), "updated"),
        source=text(dataset.get("source"), "the source"),
        dimensions=dimensions,
        units=parse_units(dataset, dimensions),
        notes=parse_notes(dataset),
    )


def cell_values(dataset: JsonObject, count: int) -> list[Decimal | None]:
    """Return the value of every cell.

    JSON-stat also allows a sparse mapping, which SSB does not send. The client accepts
    only the dense list, so a response in another form fails instead of being guessed.
    """
    cells = arr(dataset.get("value"), "value")
    if len(cells) != count:
        raise InvalidResponseError(f"the data has {len(cells)} values for {count} cells")
    for value in cells:
        if value is not None and not isinstance(value, Decimal):
            raise InvalidResponseError("a value is neither a number nor missing")
    return cast("list[Decimal | None]", cells)


def cell_symbols(dataset: JsonObject, count: int) -> list[str | None]:
    """Return the symbol of every cell, from a status mapping of cell index to symbol.

    As with values, the client accepts only the form that SSB sends.
    """
    found: list[str | None] = [None] * count
    for key, symbol in obj(dataset.get("status") or {}, "status").items():
        if not key.isdigit() or int(key) >= count:
            raise InvalidResponseError(f"{key!r} is not the index of a cell")
        found[int(key)] = text(symbol, "a symbol")
    return found


def parse_data(document: object, table_id: str) -> DataResult:
    dataset = obj(document, "the data")
    dimensions = parse_dimensions(dataset)
    sizes = [whole(size, "a size") for size in arr(dataset.get("size"), "size")]
    if sizes != [len(dimension.values) for dimension in dimensions]:
        raise InvalidResponseError("the sizes do not match the dimensions")
    count = math.prod(sizes)
    values = cell_values(dataset, count)
    symbols = cell_symbols(dataset, count)
    # JSON-stat stores cells in row-major order, the last dimension changing fastest,
    # which is the order of itertools.product.
    combinations = itertools.product(*([value.code for value in d.values] for d in dimensions))
    observations = tuple(
        Observation(codes=codes, value=value, symbol=symbol)
        for codes, value, symbol in zip(combinations, values, symbols, strict=True)
    )
    return DataResult(
        table_id=table_id,
        title=text(dataset.get("label"), "the table label"),
        updated=timestamp(dataset.get("updated"), "updated"),
        source=text(dataset.get("source"), "the source"),
        dimensions=dimensions,
        units=parse_units(dataset, dimensions),
        observations=observations,
        notes=parse_notes(dataset),
    )
