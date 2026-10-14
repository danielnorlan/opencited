"""The OpenCited MCP server: StatBank's tables, with provenance on every value (spec 0001).

The server offers three tools in a fixed order. Each declares an output schema built
from the same models that the code uses, and returns structured content. Invalid input
and the errors of the source reach the client as tool errors with the reason, so a
model can correct its call.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import Annotated

import anyio
import anyio.to_thread
import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from opencited import __version__
from opencited.http import make_client
from opencited.http.cache import ResponseCache
from opencited.model import TableId
from opencited.snapshots import SnapshotError, SnapshotStore
from opencited.sources import (
    Language,
    QueryResult,
    QueryRetrieval,
    SearchResult,
    SourceError,
    TableMetadata,
)
from opencited.sources.ssb import BASE_URL, MAX_CELLS, MAX_SEARCH_LIMIT, SsbClient

logger = logging.getLogger("opencited.mcp")

INSTRUCTIONS = (
    "OpenCited serves official statistics from Statistics Norway (SSB). Find a table "
    "with search_tables, read its dimensions and codes with get_table_metadata, then "
    "fetch values with query_table. Values are decimal strings, and every query result "
    "has a provenance record: cite the table, its link and the retrieval time with any "
    "number you use."
)
SEARCH_TABLES = (
    "Search StatBank for tables. Returns the matching tables with their ID, title, first "
    "and last period, last update and link, and how many tables matched in all."
)
GET_TABLE_METADATA = (
    "Describe a table before you query it. Returns its title, link and last update, its "
    "dimensions with their codes and labels and whether each can be left out, the unit "
    "of each content, and notes."
)
QUERY_TABLE = (
    f"Fetch values from a table, with a provenance record. A query covers at most "
    f"{MAX_CELLS:,} cells. Each cell has one code per dimension, and a value as a decimal "
    "string, or null with SSB's symbol, which symbols explains. The provenance record "
    "names the source, the licence, the table, the request, the retrieval time in UTC "
    "and the SHA-256 of SSB's response."
)
# The tools only read public data from SSB, and the same call gives the same answer
# until SSB publishes new figures.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

Query = Annotated[str, Field(description="Words to search for, in Norwegian or English.")]
Lang = Annotated[Language, Field(description="The language of titles and labels: no or en.")]
Limit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description=f"The most tables to return, 1 to {MAX_SEARCH_LIMIT}.",
    ),
]
Table = Annotated[TableId, Field(description="The five-digit ID of a StatBank table.")]
Selection = Annotated[
    dict[str, list[str]],
    Field(
        description=(
            "Maps dimension codes to lists of codes from get_table_metadata. A code can "
            "also be an expression: * or ? masks, top(n), top(n,offset), bottom(n), "
            "range(first,last), from(first) or to(last). For time, top selects the "
            "latest periods. Dimensions that can be eliminated may be left out."
        )
    ),
]


async def answer[T](call: Awaitable[T]) -> T:
    """Await a source call and turn errors that a caller can act on into tool errors."""
    try:
        return await call
    except ValidationError:
        # A model that refuses a value here is a fault in this package, which the SDK
        # reports without the text. ValidationError is a ValueError, so it goes first.
        raise
    except (SourceError, ValueError) as error:
        raise ToolError(str(error)) from error


def build_server(
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    revision_period: bool = True,
    cache: ResponseCache | None = None,
    snapshots: SnapshotStore | None = None,
) -> MCPServer[SsbClient]:
    """Return the server, which caches SSB's responses in ``cache`` when one is given.

    With ``snapshots``, every ``query_table`` result is also kept as a snapshot
    (spec 0002). ``transport`` and ``revision_period`` serve recorded responses.
    """

    @asynccontextmanager
    async def lifespan(server: MCPServer[SsbClient]) -> AsyncGenerator[SsbClient]:
        async with make_client(BASE_URL, transport=transport) as http:
            client = SsbClient(http, revision_period=revision_period, cache=cache)
            async with anyio.create_task_group() as tasks:
                # In the background, so that a slow answer from SSB does not hold up
                # the client's handshake.
                tasks.start_soon(log_versions, client)
                yield client
                tasks.cancel_scope.cancel()

    server = MCPServer(
        name="opencited",
        title="OpenCited",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/danielnorlan/opencited",
        version=__version__,
        lifespan=lifespan,
    )

    @server.tool(
        name="search_tables",
        description=SEARCH_TABLES,
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def search_tables(
        query: Query, ctx: Context[SsbClient], language: Lang = "no", limit: Limit = 10
    ) -> SearchResult:
        return await answer(ctx.request_context.lifespan_context.search(query, language, limit))

    @server.tool(
        name="get_table_metadata",
        description=GET_TABLE_METADATA,
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def get_table_metadata(
        table_id: Table, ctx: Context[SsbClient], language: Lang = "no"
    ) -> TableMetadata:
        return await answer(ctx.request_context.lifespan_context.metadata(table_id, language))

    @server.tool(
        name="query_table",
        description=QUERY_TABLE,
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def query_table(
        table_id: Table, selection: Selection, ctx: Context[SsbClient], language: Lang = "no"
    ) -> QueryResult:
        client = ctx.request_context.lifespan_context
        retrieval = await answer(client.retrieve(table_id, selection, language))
        if snapshots is not None:
            await keep(snapshots, retrieval)
        return retrieval.result

    return server


async def keep(snapshots: SnapshotStore, retrieval: QueryRetrieval) -> None:
    """Write a result's snapshot. A result without one is still returned, with a warning.

    Every failure is caught, since the snapshot is kept beside the result that the
    client asked for and must never cost it that result.
    """
    snapshot_id = retrieval.result.provenance.response_sha256
    try:
        await anyio.to_thread.run_sync(snapshots.write, retrieval)
    except SnapshotError as error:
        logger.warning("No snapshot of %s was written: %s", snapshot_id, error)
    except Exception:
        logger.exception("No snapshot of %s was written", snapshot_id)


async def log_versions(client: SsbClient) -> None:
    """Log the versions that SSB's API reports, since its behaviour can change (ADR 0007).

    The server works without them, so no failure here may stop it.
    """
    try:
        config = await client.config()
    except SourceError as error:
        logger.warning("SSB's configuration could not be read: %s", error)
        return
    except Exception:
        logger.exception("SSB's configuration could not be read")
        return
    logger.info(
        "SSB PxWebApi %s, software %s, %s cells per request",
        config.api_version,
        config.app_version,
        f"{config.max_data_cells:,}",
    )


__all__ = ["INSTRUCTIONS", "build_server"]
