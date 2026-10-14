"""The command line: ``opencited mcp`` serves the MCP server, ``opencited ask`` answers.

Without ``--http`` the server speaks MCP over stdio, as clients that start it expect,
and logs go to standard error, since standard output carries the protocol. With
``--http`` it serves Streamable HTTP at ``/mcp`` on a loopback address only. It
refuses a request whose ``Origin`` is not a local page, as the MCP specification
requires, and one whose ``Host`` is not a loopback name, which the SDK adds against
DNS rebinding.

SSB's metadata and data responses are cached in the user's cache directory, or in
``--cache-dir``, unless ``--no-cache`` is given (ADR 0008).

With ``--snapshot-dir`` or the variable ``OPENCITED_SNAPSHOT_DIR``, every
``query_table`` result is also kept as a snapshot there (spec 0002). No directory is
set by default.

``--replay DIR`` serves the responses recorded in a directory instead of calling SSB,
without a cache. It exists for tests (ADR 0008), and the server warns when it starts
with it.

``opencited ask "<question>"`` answers with the model that the ``OPENCITED_MODEL_*``
variables name (spec 0004), the same tools in the same process, and snapshots in the
user's data directory unless ``--snapshot-dir`` or the variable names another. It
prints the answer with numbered sources, or with ``--json`` every record of it. The
exit status is 0 for an answer, 3 for a refusal, 1 for a run that failed and 2 for
configuration that cannot be used.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import cast

import anyio
import platformdirs
from mcp import Client
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from opencited import __version__
from opencited.agent.answer import Answer, Refusal
from opencited.agent.config import ConfigurationError, ModelConfig, from_environment
from opencited.agent.language import language_of
from opencited.agent.loop import AgentError, ask
from opencited.agent.model import ModelClient, ModelError
from opencited.agent.render import text_of
from opencited.agent.tools import Toolbox
from opencited.http.cache import ResponseCache
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.snapshots import SnapshotError, SnapshotStore
from opencited.sources import Language
from opencited.sources.ssb import SsbClient

logger = logging.getLogger("opencited.cli")

LOOPBACK = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 8765
CACHE_FILE = "responses.sqlite"
SNAPSHOT_DIR = "OPENCITED_SNAPSHOT_DIR"
# Requests must name a loopback address in Host, and come from a local page if they
# carry an Origin. The SDK applies the same settings for a loopback host by default;
# they are set here so that the server does not depend on that default.
SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
)


def port_number(text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port number") from None
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError(f"a port is from 1 to 65535, not {number}")
    return number


def directory(text: str) -> Path:
    path = Path(text)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"{text!r} is not a directory")
    return path


def default_cache_dir() -> Path:
    """Return the user's cache directory for OpenCited, which differs by system."""
    return platformdirs.user_cache_path("opencited", appauthor=False)


def default_snapshot_dir() -> Path:
    """Return the snapshot directory of ``opencited ask`` in the user's data directory."""
    return platformdirs.user_data_path("opencited", appauthor=False) / "snapshots"


def add_sources(command: argparse.ArgumentParser, snapshots: str) -> None:
    """Add the options for the cache, the snapshots and recorded responses."""
    caching = command.add_mutually_exclusive_group()
    caching.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="directory of the response cache (default: the user's cache directory)",
    )
    caching.add_argument("--no-cache", action="store_true", help="send every request to SSB")
    command.add_argument(
        "--snapshot-dir",
        type=Path,
        metavar="DIR",
        help=f"keep every query result as a snapshot in DIR (default: ${SNAPSHOT_DIR}, or {snapshots})",
    )
    command.add_argument(
        "--replay",
        type=directory,
        metavar="DIR",
        help="serve the responses recorded in DIR instead of calling SSB (for tests)",
    )


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="opencited", description="Official statistics with provenance, over MCP."
    )
    parser.add_argument("--version", action="version", version=f"opencited {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("mcp", help="serve the MCP server over stdio or HTTP")
    serve.add_argument("--http", action="store_true", help="serve Streamable HTTP at /mcp")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        choices=LOOPBACK,
        help="loopback address for --http (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--port",
        type=port_number,
        default=DEFAULT_PORT,
        help=f"port for --http (default: {DEFAULT_PORT})",
    )
    add_sources(serve, "none")
    question = commands.add_parser(
        "ask", help="answer a question with numbers from SSB, each with its source"
    )
    question.add_argument("question", help="the question, in Norwegian or English")
    question.add_argument(
        "--language",
        choices=("no", "en"),
        help="the language of the answer (default: the language of the question)",
    )
    question.add_argument("--json", action="store_true", help="print every record as JSON")
    add_sources(question, "the user's data directory")
    return parser.parse_args(arguments)


def cache_of(options: argparse.Namespace) -> ResponseCache | None:
    if options.no_cache:
        return None
    directory = cast("Path | None", options.cache_dir) or default_cache_dir()
    logger.info("Caching SSB's responses in %s", directory / CACHE_FILE)
    return ResponseCache(directory / CACHE_FILE)


def snapshots_of(options: argparse.Namespace, default: Path | None = None) -> SnapshotStore | None:
    """Return the snapshot store that the options or the variable name, or None.

    The directory is checked at start, so that a directory that cannot be written stops
    the server instead of leaving every result without a snapshot.
    """
    directory = cast("Path | None", options.snapshot_dir)
    if directory is None and os.environ.get(SNAPSHOT_DIR):
        directory = Path(os.environ[SNAPSHOT_DIR])
    directory = directory or default
    if directory is None:
        return None
    store = SnapshotStore(directory)
    store.check()
    logger.info("Keeping a snapshot of every query result in %s", directory)
    return store


def server_of(options: argparse.Namespace, snapshots: SnapshotStore | None) -> MCPServer[SsbClient]:
    if options.replay is None:
        return build_server(cache=cache_of(options), snapshots=snapshots)
    logger.warning(
        "Serving the responses recorded in %s instead of calling SSB. They are not "
        "live data, and each provenance record gives the time of the replay.",
        options.replay,
    )
    # The recordings were taken outside SSB's revision period, so replaying them is
    # safe at any hour, and the check would only block tests run at those hours.
    return build_server(
        transport=replay_transport(options.replay), revision_period=False, snapshots=snapshots
    )


async def answer(
    question: str,
    language: Language,
    model: ModelClient,
    server: MCPServer[SsbClient],
    store: SnapshotStore,
) -> Answer | Refusal | AgentError:
    """Return the answer, the refusal, or the error that stopped the run.

    The error is returned, since raised inside the MCP client's task group it would
    leave wrapped in an exception group.
    """
    async with Client(server) as client, model:
        toolbox = await Toolbox.connect(client, store, language)
        try:
            return await ask(question, language, model=model, toolbox=toolbox)
        except AgentError as error:
            return error


def ask_command(options: argparse.Namespace) -> int:
    try:
        config: ModelConfig = from_environment(os.environ)
        store = snapshots_of(options, default_snapshot_dir())
        # Last, since the client holds an HTTP connection pool until the run closes it.
        model = ModelClient(config)
    except (ConfigurationError, ModelError, SnapshotError) as error:
        print(f"opencited: {error}", file=sys.stderr)
        return 2
    if store is None:
        raise ValueError("ask always has a snapshot store")
    question = cast("str", options.question)
    language: Language = options.language or language_of(question)
    server = server_of(options, store)
    result = anyio.run(answer, question, language, model, server, store)
    if isinstance(result, AgentError):
        print(f"opencited: the run failed: {result}", file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2) if options.json else text_of(result))
    return 0 if isinstance(result, Answer) else 3


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    asking = options.command == "ask"
    logging.basicConfig(
        stream=sys.stderr,
        # The answer is the output of ask; only warnings and errors join it.
        level=logging.WARNING if asking else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The HTTP library logs every request at INFO, which buries the server's own log.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if asking:
        return ask_command(options)
    try:
        snapshots = snapshots_of(options)
    except SnapshotError as error:
        print(f"opencited: {error}", file=sys.stderr)
        return 2
    server = server_of(options, snapshots)
    if options.http:
        server.run(
            "streamable-http", host=options.host, port=options.port, transport_security=SECURITY
        )
    else:
        server.run()
    return 0
