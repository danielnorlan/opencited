#!/usr/bin/env python3
"""Start the server from a built wheel in a clean environment and call every tool.

Usage: python scripts/smoke_package.py WHEEL

The server runs as `uvx --isolated --from WHEEL opencited mcp`, so it uses only the
wheel and the dependencies it declares, and it replays the recordings in
`tests/fixtures/ssb/`, so the check needs no access to SSB. The script lists the tools,
calls each of them over stdio, prints one line per problem and exits with 1 when there
is one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ssb"
TOOLS = ["search_tables", "get_table_metadata", "query_table"]
# Calls that the recordings answer.
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_tables", {"query": "population", "language": "en"}),
    ("get_table_metadata", {"table_id": "05810", "language": "en"}),
    (
        "query_table",
        {
            "table_id": "05810",
            "selection": {
                "Kjonn": ["0"],
                "Alder": ["999B"],
                "ContentsCode": ["Personer"],
                "Tid": ["2025", "2026"],
            },
            "language": "en",
        },
    ),
]


def command(wheel: Path) -> list[str]:
    """Return the command that starts the server from a wheel in a clean environment."""
    return [
        "uvx",
        "--isolated",
        "--from",
        str(wheel),
        "opencited",
        "mcp",
        "--replay",
        str(FIXTURES),
    ]


async def problems(server: list[str]) -> list[str]:
    """Return what went wrong when the server was started and every tool was called."""
    found: list[str] = []
    async with Client(StdioServerParameters(command=server[0], args=server[1:])) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
        if names != TOOLS:
            found.append(f"the server lists {names}, not {TOOLS}")
        for name, arguments in CALLS:
            result = await client.call_tool(name, arguments)
            if result.is_error or result.structured_content is None:
                found.append(f"{name} gave no result: {result.content}")
    return found


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("Usage: python scripts/smoke_package.py WHEEL", file=sys.stderr)
        return 2
    found = asyncio.run(problems(command(Path(arguments[0]))))
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
