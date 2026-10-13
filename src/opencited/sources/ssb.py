"""Statistics Norway's StatBank through PxWebApi 2 (ADR 0007).

The client searches tables, reads their metadata and fetches data. Responses are parsed
from their raw bytes with decimals (ADR 0009), and every result is a frozen model. The
client checks table IDs, languages, limits and selections before it sends anything, so
invalid input never reaches SSB.
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
    SourceError,
    SourceUnavailableError,
    TableNotFoundError,
)
from opencited.sources.models import (
    LANGUAGES,
    DataResult,
    Dimension,
    Frozen,
    Language,
    Observation,
    Retrieved,
    SearchResult,
    TableMetadata,
    TableSummary,
    Unit,
    Value,
)
from opencited.values import load_json

BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
# A GET URL of about 3,300 characters got an HTML error page from SSB, and its guide
# gives about 2,100 as the limit. Below 1,800 a request stays a link that a reader can
# open from the provenance record (ADR 0007).
MAX_GET_URL_LENGTH = 1800
TABLE_ID = re.compile(r"[0-9]{5}")
MAX_SEARCH_LIMIT = 50
# The title of SSB's error document when a table does not exist. A metadata request
# gets it with HTTP 404 and a data request with HTTP 400.
NO_SUCH_TABLE = "Non-existent table"
# Whole numbers that describe structure, such as sizes and positions, stay far below
# this. A larger one would make ``int`` slow and cannot describe a real table.
MAX_WHOLE_DIGITS = 18

type JsonObject = dict[str, object]
type Params = tuple[tuple[str, str], ...]


class ApiConfig(Frozen):
    """What `/config` reports about the API."""

    api_version: str
    app_version: str
    max_data_cells: int
    languages: tuple[str, ...]
    licence_url: str


def check_table_id(table_id: str) -> None:
    """Reject a table ID that is not five ASCII digits, before any request is sent."""
    if not TABLE_ID.fullmatch(table_id):
        raise ValueError(f"a table ID has five digits, not {table_id!r}")


def check_language(language: str) -> None:
    """Reject a language that SSB does not serve, before any request is sent."""
    if language not in LANGUAGES:
        raise ValueError(f"the language is 'no' or 'en', not {language!r}")


def check_selection(selection: Mapping[str, Sequence[str]]) -> None:
    """Reject a selection that is empty or holds codes that the URL would change."""
    if not selection:
        raise ValueError("a selection needs at least one dimension")
    for dimension, codes in selection.items():
        if isinstance(codes, str):
            raise ValueError(f"the codes of {dimension} must be a list, not a string")
        if not codes or any(not code for code in codes):
            raise ValueError(f"every code of {dimension} must be a non-empty string")
        if any(code.startswith("[") and code.endswith("]") for code in codes):
            raise ValueError(f"write the codes of {dimension} without brackets")


def url_codes(codes: Sequence[str]) -> str:
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
        *((f"valueCodes[{dimension}]", url_codes(codes)) for dimension, codes in selection.items()),
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
        if not str(http.base_url).startswith("https://"):
            raise ValueError("the HTTP client needs SSB's base URL, BASE_URL")
        self._http = http
        self._max_get_url_length = max_get_url_length

    async def config(self) -> ApiConfig:
        """Return what the API reports about itself."""
        document, _ = await self._fetch(self._http.build_request("GET", "/config"))
        return parse_config(document)

    async def search(self, query: str, language: Language = "no", limit: int = 10) -> SearchResult:
        """Return the tables that match a free-text query, in SSB's order."""
        check_language(language)
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
        check_language(language)
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
        """Return the cells of a selection, which maps dimension codes to lists of codes.

        A code can also be one of SSB's selection expressions, such as ``*`` or
        ``top(3)``, written without brackets.
        """
        check_table_id(table_id)
        check_language(language)
        check_selection(selection)
        request = data_request(self._http, table_id, selection, language, self._max_get_url_length)
        document, exchange = await self._fetch(request, table_id)
        return Retrieved(parse_data(document, table_id), exchange)

    async def _fetch(
        self, request: httpx2.Request, table_id: str | None = None
    ) -> tuple[object, Exchange]:
        """Send a request and return its JSON body, or raise the error that fits.

        The status decides first. A body is parsed for a success, and for a client
        error only to read the reason that SSB gives.
        """
        try:
            exchange = await send(self._http, request)
        except httpx2.TimeoutException as error:
            raise SourceUnavailableError("SSB did not answer in time") from error
        except httpx2.DecodingError as error:
            raise InvalidResponseError("SSB's answer could not be decoded") from error
        except httpx2.RequestError as error:
            raise SourceUnavailableError("SSB could not be reached") from error
        status = exchange.status
        json_body = "json" in exchange.content_type
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimitedError("SSB's rate limit was reached", retry_after(exchange))
        if HTTPStatus.BAD_REQUEST <= status < HTTPStatus.INTERNAL_SERVER_ERROR:
            raise client_error(exchange, table_id)
        if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
            raise SourceUnavailableError(f"SSB answered with HTTP {status}")
        if not json_body:
            raise SourceUnavailableError(f"SSB answered with HTTP {status} and no JSON body")
        try:
            document = load_json(exchange.content)
        except ValueError as error:
            raise InvalidResponseError("SSB's answer is not valid JSON") from error
        return document, exchange


def client_error(exchange: Exchange, table_id: str | None) -> SourceError:
    """Return the error for an HTTP status from 400 to 499 other than 429."""
    if "json" not in exchange.content_type:
        return SourceUnavailableError(f"SSB answered with HTTP {exchange.status} and no JSON body")
    title = problem_title(exchange)
    if title == NO_SUCH_TABLE:
        return TableNotFoundError(f"SSB has no table {table_id}" if table_id else title)
    return RejectedRequestError(f"SSB rejected the request: {title or 'no reason given'}")


def retry_after(exchange: Exchange) -> int | None:
    """Return the seconds in a Retry-After header, or None when it holds no such number."""
    value = exchange.header("retry-after")
    if value is not None and value.isascii() and value.isdigit():
        return int(value)
    return None


def problem_title(exchange: Exchange) -> str | None:
    """Return the title of SSB's error document, or None when the body has none."""
    try:
        document = load_json(exchange.content)
    except ValueError:
        return None
    if isinstance(document, dict):
        title = cast("JsonObject", document).get("title")
        if isinstance(title, str):
            return title
    return None


# Parsing. JSON from `load_json` holds every number as a Decimal, so whole numbers that
# describe structure are converted here and checked.


def _obj(value: object, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise InvalidResponseError(f"{what} must be an object")
    return cast("JsonObject", value)


def _arr(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidResponseError(f"{what} must be a list")
    return cast("list[object]", value)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise InvalidResponseError(f"{what} must be a string")
    return value


def _texts(value: object, what: str) -> tuple[str, ...]:
    return tuple(_text(item, what) for item in _arr(value, what))


def _whole(value: object, what: str) -> int:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.to_integral_value()
        or value.adjusted() >= MAX_WHOLE_DIGITS
    ):
        raise InvalidResponseError(f"{what} must be a whole number")
    return int(value)


def _timestamp(value: object, what: str) -> datetime:
    try:
        moment = datetime.fromisoformat(_text(value, what))
    except ValueError as error:
        raise InvalidResponseError(f"{what} must be a timestamp") from error
    if moment.tzinfo is None:
        raise InvalidResponseError(f"{what} must have a time zone")
    return moment


def title_of(label: str, table_id: str) -> str:
    """Return a table's title without the ``"05810: "`` that SSB puts before it."""
    return label.removeprefix(f"{table_id}: ")


def parse_config(document: object) -> ApiConfig:
    config = _obj(document, "the configuration")
    return ApiConfig(
        api_version=_text(config.get("apiVersion"), "apiVersion"),
        app_version=_text(config.get("appVersion"), "appVersion"),
        max_data_cells=_whole(config.get("maxDataCells"), "maxDataCells"),
        languages=tuple(
            _text(_obj(item, "a language").get("id"), "a language ID")
            for item in _arr(config.get("languages"), "the list of languages")
        ),
        licence_url=_text(config.get("license"), "license"),
    )


def parse_search(document: object) -> SearchResult:
    found = _obj(document, "the search result")
    summaries: list[TableSummary] = []
    for item in _arr(found.get("tables"), "the list of tables"):
        table = _obj(item, "a table")
        table_id = _text(table.get("id"), "a table ID")
        time_unit = table.get("timeUnit")
        summaries.append(
            TableSummary(
                table_id=table_id,
                title=title_of(_text(table.get("label"), "a table label"), table_id),
                first_period=_text(table.get("firstPeriod"), "firstPeriod"),
                last_period=_text(table.get("lastPeriod"), "lastPeriod"),
                updated=_timestamp(table.get("updated"), "updated"),
                time_unit=time_unit if isinstance(time_unit, str) else None,
                variables=_texts(table.get("variableNames", []), "variableNames"),
            )
        )
    total = _whole(_obj(found.get("page"), "page").get("totalElements"), "totalElements")
    return SearchResult(total=total, tables=tuple(summaries))


def ordered_codes(index: object, what: str) -> list[str]:
    """Return the codes of a JSON-stat category in their order.

    SSB sends the index as a mapping from code to position, and the positions must run
    from zero without gaps.
    """
    positions = _obj(index, what)
    order = sorted((_whole(position, what), code) for code, position in positions.items())
    if [position for position, _ in order] != list(range(len(order))):
        raise InvalidResponseError(f"{what} has gaps in its positions")
    return [code for _, code in order]


def parse_dimensions(dataset: JsonObject) -> tuple[Dimension, ...]:
    role = _obj(dataset.get("role", {}), "role")
    time = set(_texts(role.get("time", []), "role.time"))
    metric = set(_texts(role.get("metric", []), "role.metric"))
    described = _obj(dataset.get("dimension"), "dimension")
    dimensions: list[Dimension] = []
    for dimension_id in _texts(dataset.get("id"), "id"):
        dimension = _obj(described.get(dimension_id), f"dimension {dimension_id}")
        category = _obj(dimension.get("category"), f"the category of {dimension_id}")
        labels = _obj(category.get("label", {}), f"the labels of {dimension_id}")
        value_notes = _obj(category.get("note") or {}, f"the notes of {dimension_id}")
        extension = _obj(dimension.get("extension", {}), f"the extension of {dimension_id}")
        codes = ordered_codes(category.get("index"), f"the index of {dimension_id}")
        dimensions.append(
            Dimension(
                code=dimension_id,
                label=_text(dimension.get("label"), f"the label of {dimension_id}"),
                values=tuple(
                    Value(
                        code=code,
                        label=_text(labels.get(code, code), f"a label of {dimension_id}"),
                        notes=_texts(value_notes.get(code, []), f"a note of {dimension_id}"),
                    )
                    for code in codes
                ),
                notes=_texts(dimension.get("note") or [], f"the notes of {dimension_id}"),
                time=dimension_id in time,
                contents=dimension_id in metric,
                eliminable=extension.get("elimination") is True,
            )
        )
    return tuple(dimensions)


def parse_units(dataset: JsonObject, dimensions: tuple[Dimension, ...]) -> tuple[Unit, ...]:
    described = _obj(dataset.get("dimension"), "dimension")
    units: list[Unit] = []
    for dimension in dimensions:
        if not dimension.contents:
            continue
        dimension_object = _obj(described.get(dimension.code), dimension.code)
        category = _obj(dimension_object.get("category"), f"the category of {dimension.code}")
        for code, unit in _obj(category.get("unit", {}), f"the units of {dimension.code}").items():
            described_unit = _obj(unit, f"the unit of {code}")
            units.append(
                Unit(
                    code=code,
                    base=_text(described_unit.get("base"), f"the unit base of {code}"),
                    decimals=_whole(described_unit.get("decimals"), f"the decimals of {code}"),
                )
            )
    return tuple(units)


def parse_described(dataset: JsonObject, table_id: str) -> dict[str, object]:
    """Return the fields that metadata and data results share."""
    dimensions = parse_dimensions(dataset)
    return {
        "table_id": table_id,
        "title": title_of(_text(dataset.get("label"), "the table label"), table_id),
        "updated": _timestamp(dataset.get("updated"), "updated"),
        "source": _text(dataset.get("source"), "the source"),
        "dimensions": dimensions,
        "units": parse_units(dataset, dimensions),
        "notes": _texts(dataset.get("note") or [], "note"),
    }


def parse_metadata(document: object, table_id: str) -> TableMetadata:
    return TableMetadata.model_validate(parse_described(_obj(document, "the metadata"), table_id))


def cell_values(dataset: JsonObject, count: int) -> list[Decimal | None]:
    """Return the value of every cell.

    JSON-stat also allows a sparse mapping, which SSB does not send. The client accepts
    only the dense list, so a response in another form fails instead of being guessed.
    """
    cells = _arr(dataset.get("value"), "value")
    if len(cells) != count:
        raise InvalidResponseError(f"the data has {len(cells)} values for {count} cells")
    for value in cells:
        if value is not None and not isinstance(value, Decimal):
            raise InvalidResponseError("a value must be a number or missing")
    return cast("list[Decimal | None]", cells)


def cell_symbols(dataset: JsonObject, count: int) -> list[str | None]:
    """Return the symbol of every cell, from a status mapping of cell index to symbol.

    As with values, the client accepts only the form that SSB sends.
    """
    found: list[str | None] = [None] * count
    for key, symbol in _obj(dataset.get("status") or {}, "status").items():
        if not (key.isascii() and key.isdigit()) or int(key) >= count:
            raise InvalidResponseError(f"{key!r} is not the index of a cell")
        found[int(key)] = _text(symbol, "a symbol")
    return found


def parse_data(document: object, table_id: str) -> DataResult:
    dataset = _obj(document, "the data")
    shared = parse_described(dataset, table_id)
    dimensions = cast("tuple[Dimension, ...]", shared["dimensions"])
    sizes = [_whole(size, "a size") for size in _arr(dataset.get("size"), "size")]
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
    return DataResult.model_validate({**shared, "observations": observations})
