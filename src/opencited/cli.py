"""The command line: ``opencited mcp`` serves the MCP server.

Without ``--http`` the server speaks MCP over stdio, as clients that start it expect,
and logs go to standard error, since standard output carries the protocol. With
``--http`` it serves Streamable HTTP at ``/mcp`` on a loopback address only. It
refuses a request whose ``Origin`` is not a local page, as the MCP specification
requires, and one whose ``Host`` is not a loopback name, which the SDK adds against
DNS rebinding.

``--replay DIR`` serves the responses recorded in a directory instead of calling SSB.
It exists for tests (ADR 0008), and the server warns when it starts with it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings

from opencited import __version__
from opencited.http.replay import replay_transport
from opencited.mcp import build_server

logger = logging.getLogger("opencited.cli")

LOOPBACK = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 8765
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
    serve.add_argument(
        "--replay",
        type=directory,
        metavar="DIR",
        help="serve the responses recorded in DIR instead of calling SSB (for tests)",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    # The HTTP library logs every request at INFO, which buries the server's own log.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if options.replay is None:
        server = build_server()
    else:
        logger.warning(
            "Serving the responses recorded in %s instead of calling SSB. They are not "
            "live data, and each provenance record gives the time of the replay.",
            options.replay,
        )
        # The recordings were taken outside SSB's revision period, so replaying them is
        # safe at any hour, and the check would only block tests run at those hours.
        server = build_server(transport=replay_transport(options.replay), revision_period=False)
    if options.http:
        server.run(
            "streamable-http", host=options.host, port=options.port, transport_security=SECURITY
        )
    else:
        server.run()
    return 0
