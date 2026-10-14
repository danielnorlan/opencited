#!/usr/bin/env python3
"""Check that the release metadata agrees: the package, `server.json` and the README.

Usage: python scripts/check_release.py [--tag TAG] [--root DIR]

The MCP Registry lists the server from `server.json`, and it accepts a PyPI package
only when the package's README holds the line `mcp-name: NAME` with the server's name.
The script checks that `server.json` uses the registry's schema of 2025-12-11, that its
version and its PyPI package match `pyproject.toml`, and that the README names the
server. With ``--tag`` it also checks that a release tag is ``v`` and the version. It
prints one line per problem and exits with 1 when there is one. The release workflow
validates `server.json` against the schema itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import cast

SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
# The registry's schema allows at most this many characters.
MAX_DESCRIPTION = 100

type Json = dict[str, object]


def load(root: Path) -> tuple[Json, Json, str]:
    """Return the project table of `pyproject.toml`, `server.json` and the README."""
    with (root / "pyproject.toml").open("rb") as file:
        project = cast("Json", tomllib.load(file)["project"])
    server = cast("Json", json.loads((root / "server.json").read_text(encoding="utf-8")))
    readme = (root / "README.md").read_text(encoding="utf-8")
    return project, server, readme


def problems(project: Json, server: Json, readme: str, tag: str | None = None) -> list[str]:
    """Return every disagreement between the package, `server.json`, the README and a tag."""
    found: list[str] = []
    name, version = project.get("name"), project.get("version")
    if server.get("$schema") != SCHEMA:
        found.append(f"server.json does not use the schema {SCHEMA}")
    if server.get("version") != version:
        found.append(f"server.json has version {server.get('version')!r}, the package {version!r}")
    description = server.get("description")
    if not isinstance(description, str) or not 0 < len(description) <= MAX_DESCRIPTION:
        found.append(f"the description in server.json needs 1 to {MAX_DESCRIPTION} characters")
    packages = cast("list[Json]", server.get("packages") or [])
    pypi = [package for package in packages if package.get("registryType") == "pypi"]
    if len(pypi) != 1:
        found.append("server.json needs exactly one PyPI package")
    else:
        [package] = pypi
        if package.get("identifier") != name:
            found.append(f"the PyPI package in server.json is not {name!r}")
        if package.get("version") != version:
            found.append(f"the PyPI package in server.json is not version {version!r}")
    server_name = server.get("name")
    line = re.compile(rf"(?m)^(?:<!--\s*)?mcp-name: {re.escape(str(server_name))}(?:\s*-->)?\s*$")
    if not isinstance(server_name, str) or not line.search(readme):
        found.append(f"the README has no line 'mcp-name: {server_name}'")
    if tag is not None and tag != f"v{version}":
        found.append(f"the tag {tag!r} is not 'v{version}'")
    return found


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check that the release metadata agrees.")
    parser.add_argument("--tag", help="the release tag, such as v0.1.0")
    parser.add_argument("--root", type=Path, default=Path(), help="the repository root")
    options = parser.parse_args(arguments)
    found = problems(*load(options.root), tag=options.tag)
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
