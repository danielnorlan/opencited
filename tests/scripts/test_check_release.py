"""Tests for the release metadata check, on the repository and on altered copies."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import cast

import check_release as release
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_release.py"
PROJECT, SERVER, README = release.load(REPO_ROOT)
type Json = dict[str, object]


def package(server: Json) -> Json:
    return cast("list[Json]", server["packages"])[0]


def test_the_repository_agrees_with_itself() -> None:
    assert release.problems(PROJECT, SERVER, README) == []


def test_the_script_passes_on_the_repository_and_checks_a_tag() -> None:
    version = PROJECT["version"]
    passed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), "--tag", f"v{version}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0, passed.stdout
    failed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), "--tag", "v9.9.9"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert "the tag 'v9.9.9' is not" in failed.stdout


def altered(change: str) -> Json:
    server = copy.deepcopy(SERVER)
    match change:
        case "schema":
            server["$schema"] = release.SCHEMA.replace("2025-12-11", "2025-10-17")
        case "version":
            server["version"] = "9.9.9"
        case "package version":
            package(server)["version"] = "9.9.9"
        case "identifier":
            package(server)["identifier"] = "opencited-server"
        case "registry":
            package(server)["registryType"] = "npm"
        case "description":
            server["description"] = "x" * (release.MAX_DESCRIPTION + 1)
        case "name":
            server["name"] = "io.github.danielnorlan/other"
        case _:
            raise ValueError(change)
    return server


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("schema", "does not use the schema"),
        ("version", "server.json has version '9.9.9'"),
        ("package version", "is not version"),
        ("identifier", "the PyPI package in server.json is not 'opencited'"),
        ("registry", "exactly one PyPI package"),
        ("description", "1 to 100 characters"),
        ("name", "no line 'mcp-name: io.github.danielnorlan/other'"),
    ],
)
def test_each_disagreement_is_reported(change: str, problem: str) -> None:
    found = release.problems(PROJECT, altered(change), README)
    assert len(found) == 1
    assert problem in found[0]


@pytest.mark.parametrize(
    "line",
    [
        "mcp-name: io.github.danielnorlan/opencited",
        "<!-- mcp-name: io.github.danielnorlan/opencited -->",
    ],
)
def test_the_readme_line_may_stand_alone_or_in_a_comment(line: str) -> None:
    assert release.problems(PROJECT, SERVER, f"# Title\n\n{line}\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "mcp-name: io.github.danielnorlan/opencited-extra",
        "See mcp-name: io.github.danielnorlan/opencited in the text.",
    ],
)
def test_the_readme_line_must_name_the_server_exactly(line: str) -> None:
    [problem] = release.problems(PROJECT, SERVER, f"# Title\n\n{line}\n")
    assert "mcp-name" in problem
