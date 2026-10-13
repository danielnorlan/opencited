#!/usr/bin/env python3
"""Reject files that contain a personal email address, a path in a home directory or
the name of the machine the check runs on.

Usage: python scripts/check_personal_data.py [FILE ...]

The repository, its history and its CI logs become public with the repository, and
nothing can be removed from the history afterwards. Email addresses are allowed on
GitHub's domains and on the domains reserved for examples. The script prints one line
per finding, without the matched text, and exits with 1 when there is one.
`scripts/check_commit_message.py` applies the same rules to commit messages and pull
request text.
"""

from __future__ import annotations

import re
import socket
import sys
from functools import cache
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?P<domain>(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
# GitHub's domains cover noreply@github.com and the per-user noreply addresses.
# RFC 2606 reserves the example domains for documentation.
ALLOWED_DOMAIN = re.compile(
    r"^(?:(?:[a-z0-9-]+\.)*github\.com|example\.(?:com|org|net))$", re.IGNORECASE
)
HOME_PATH = re.compile(r"(?:/home/|/Users/|\b[A-Za-z]:\\Users\\)[A-Za-z0-9._-]+")
# Host names that say nothing about a person, such as those of CI runners.
GENERIC_HOSTS = frozenset({"localhost", "runner"})
MIN_HOST_LENGTH = 4


@cache
def host_pattern() -> re.Pattern[str] | None:
    """Return a pattern for this machine's host name, or None when it is generic."""
    name = socket.gethostname().split(".")[0].lower()
    if len(name) < MIN_HOST_LENGTH or name in GENERIC_HOSTS:
        return None
    return re.compile(rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9-])", re.IGNORECASE)


def line_findings(line: str) -> list[str]:
    """Return the kinds of personal data in one line of text."""
    findings: list[str] = []
    if any(not ALLOWED_DOMAIN.match(match["domain"]) for match in EMAIL.finditer(line)):
        findings.append("email address outside the allowed domains")
    if HOME_PATH.search(line):
        findings.append("path in a home directory")
    host = host_pattern()
    if host is not None and host.search(line):
        findings.append("name of this machine")
    return findings


def check_file(path: Path) -> list[str]:
    """Return one finding per kind of personal data on each line, as ``path:line: kind``."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return [
        f"{path}:{number}: {kind}"
        for number, line in enumerate(text.splitlines(), start=1)
        for kind in line_findings(line)
    ]


def main(arguments: list[str]) -> int:
    findings = [finding for argument in arguments for finding in check_file(Path(argument))]
    for finding in findings:
        print(finding)
    if findings:
        print(
            "Personal data does not belong in the repository. See CONTRIBUTING.md.",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
