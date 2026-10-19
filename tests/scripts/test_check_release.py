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


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_script_passes_on_the_repository() -> None:
    finished = run()
    assert finished.returncode == 0, finished.stdout


def test_the_script_checks_the_tag_of_a_release() -> None:
    finished = run("--tag", "v9.9.9")
    assert finished.returncode == 1
    assert "the tag 'v9.9.9' is not" in finished.stdout
    # The README of a release says nothing of the package not being on PyPI.
    assert "the README still says" not in finished.stdout


def test_a_release_needs_a_readme_without_the_note_before_release() -> None:
    tag = f"v{PROJECT['version']}"
    # The note that the README held until the first release.
    unreleased = f"{README}\nThe package is {release.BEFORE_RELEASE} yet.\n"
    assert release.problems(PROJECT, SERVER, README, tag) == []
    [problem] = release.problems(PROJECT, SERVER, unreleased, tag)
    assert "not on PyPI" in problem


def test_a_project_without_a_version_is_refused() -> None:
    project = {key: value for key, value in PROJECT.items() if key != "version"}
    server = copy.deepcopy(SERVER)
    del server["version"]
    del package(server)["version"]
    assert release.problems(project, server, README) == ["pyproject.toml has no version"]


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
        case "two packages":
            server["packages"] = [package(server), copy.deepcopy(package(server))]
        case "transport":
            package(server)["transport"] = {
                "type": "streamable-http",
                "url": "http://127.0.0.1:8765/mcp",
            }
        case "arguments":
            package(server)["packageArguments"] = [{"type": "positional", "value": "serve"}]
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
        ("two packages", "exactly one PyPI package"),
        ("transport", "does not run 'opencited mcp' on stdio"),
        ("arguments", "does not run 'opencited mcp' on stdio"),
        ("description", "1 to 100 characters"),
        ("name", "no line 'mcp-name: io.github.danielnorlan/other'"),
    ],
)
def test_each_disagreement_is_reported(change: str, problem: str) -> None:
    found = release.problems(PROJECT, altered(change), README)
    assert len(found) == 1
    assert problem in found[0]


def test_a_description_may_have_the_most_characters_the_schema_allows() -> None:
    server = copy.deepcopy(SERVER)
    server["description"] = "x" * release.MAX_DESCRIPTION
    assert release.problems(PROJECT, server, README) == []


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
        "See mcp-name: io.github.danielnorlan/opencited",
        "<!-- mcp-name: io.github.danielnorlan/opencited",
        "mcp-name: io.github.danielnorlan/opencited -->",
    ],
)
def test_the_readme_line_must_name_the_server_exactly(line: str) -> None:
    [problem] = release.problems(PROJECT, SERVER, f"# Title\n\n{line}\n")
    assert "mcp-name" in problem
