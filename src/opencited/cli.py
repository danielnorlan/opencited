"""The command line: ``opencited mcp`` serves the MCP server.

Without ``--http`` the server speaks MCP over stdio, as clients that start it expect.
With ``--http`` it serves Streamable HTTP at ``/mcp`` on a loopback address only, and
refuses requests whose ``Host`` or ``Origin`` is not local, as the MCP specification
requires. Logs go to standard error, since standard output carries the protocol on stdio.

The variable ``OPENCITED_SSB_FIXTURES`` points the server at a directory of recorded
responses, which it replays instead of calling SSB. Tests use it (ADR 0008).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings

from opencited import __version__
from opencited.http.replay import replay_transport
from opencited.mcp import build_server

FIXTURES = "OPENCITED_SSB_FIXTURES"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 8765
# Requests must name a loopback address in Host, and come from a local page if they
# carry an Origin. This blocks DNS rebinding, where a remote page reaches a local server.
SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
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
        "--port", type=int, default=DEFAULT_PORT, help=f"port for --http (default: {DEFAULT_PORT})"
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    # The HTTP library logs every request at INFO, which buries the server's own log.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    fixtures = os.environ.get(FIXTURES)
    if fixtures:
        # The recordings were taken outside SSB's revision period, so replaying them is
        # safe at any hour, and the check would only block tests run at those hours.
        server = build_server(transport=replay_transport(Path(fixtures)), revision_period=False)
    else:
        server = build_server()
    if options.http:
        server.run(
            "streamable-http", host=options.host, port=options.port, transport_security=SECURITY
        )
    else:
        server.run()
    return 0
