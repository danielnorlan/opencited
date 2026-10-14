"""Tests for the package smoke check. The check itself runs in CI against the built wheel."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import smoke_package as smoke

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_command_runs_the_wheel_in_a_clean_environment() -> None:
    wheel = Path("dist/opencited-0.1.0-py3-none-any.whl")
    assert smoke.command(wheel) == [
        "uvx",
        "--isolated",
        "--from",
        str(wheel),
        "opencited",
        "mcp",
        "--replay",
        str(REPO_ROOT / "tests" / "fixtures" / "ssb"),
    ]


def test_the_check_passes_for_the_server_in_the_source_tree() -> None:
    server = [sys.executable, "-m", "opencited", "mcp", "--replay", str(smoke.FIXTURES)]
    assert asyncio.run(smoke.problems(server)) == []


def test_the_check_reports_tools_that_give_no_result(tmp_path: Path) -> None:
    # An empty directory of recordings answers no request, so every tool fails.
    server = [sys.executable, "-m", "opencited", "mcp", "--replay", str(tmp_path)]
    found = asyncio.run(smoke.problems(server))
    assert [problem.split()[0] for problem in found] == smoke.TOOLS


def test_the_check_reports_a_server_with_other_tools(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        "from mcp.server import MCPServer\n"
        "server = MCPServer(name='other')\n"
        "@server.tool()\n"
        "def search_tables() -> str:\n"
        "    return 'no tables'\n"
        "server.run()\n",
        encoding="utf-8",
    )
    found = asyncio.run(smoke.problems([sys.executable, str(script)]))
    assert found[0] == f"the server lists ['search_tables'], not {smoke.TOOLS}"


def test_the_script_needs_one_wheel() -> None:
    assert smoke.main([]) == 2
