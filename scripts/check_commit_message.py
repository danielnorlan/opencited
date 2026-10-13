#!/usr/bin/env python3
"""Check commits, pull request text and branch names before they become public.

Usage:
    python scripts/check_commit_message.py [--identity] [FILE ...]
    python scripts/check_commit_message.py --pre-push
    python scripts/check_commit_message.py --range BASE..HEAD
    python scripts/check_commit_message.py --title-env NAME --env NAME
    python scripts/check_commit_message.py --branch NAME

A FILE is a commit message file, as the commit-msg hook passes it. Comment lines and
the diff below the scissors line of a verbose commit are skipped, because git leaves
them out of the message. --identity checks the author and committer that git would
record now. --pre-push runs from the pre-push hook: it checks the branch being pushed
and every commit on a local branch that the remote does not have yet, because
pre-commit reports only the first branch of a push. --range checks the message, the
author and the committer of every commit in a revision range, except commits by
Dependabot, which writes its own messages. --title-env and --env read a pull request
title and description from environment variables, which is how CI passes them
without putting them into a shell command. --branch checks a branch name.

The rules: authors and committers use a GitHub noreply address; a commit message has
no trailers; a subject starts with a capital letter and has no full stop, with at most
72 characters for a commit and 64 for a pull request title, since GitHub appends the
pull request number; branch names follow <issue-number>-<short-slug>; and no text
contains personal data (`scripts/check_personal_data.py`). Findings never print the
matched text, because CI logs become public with the repository. The script prints one
line per finding and exits with 1 when there is one. CONTRIBUTING.md and ADR 0006 give
the rules.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from check_personal_data import line_findings as personal_data_in

# Each commit has one author. GitHub adds co-author lines to squash commits for every
# other author, so no line in a message adds another person or a sign-off.
TRAILER = re.compile(r"^\s*[A-Za-z][A-Za-z-]*-by:\s", re.IGNORECASE)
# GitHub's per-user noreply addresses, and the address GitHub commits with when it
# creates a squash commit.
NOREPLY = re.compile(r"@users\.noreply\.github\.com$|^noreply@github\.com$", re.IGNORECASE)
IDENT = re.compile(r"^(?P<name>.*?) <(?P<email>[^>]*)>")
BRANCH = re.compile(r"^[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*$")
MAIN = "main"
MAX_SUBJECT = 72
MAX_TITLE = 64
DEPENDABOT = "dependabot[bot] <"
DEPENDABOT_BRANCH = "dependabot/"
SCISSORS = "# ------------------------ >8 ------------------------"
FIELD = "\x1f"
RECORD = "\x1e"


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    committer: str
    message: str


def message_lines(text: str) -> Iterable[tuple[int, str]]:
    """Yield the numbered lines that git keeps when it records a commit message."""
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(SCISSORS):
            return
        if not line.startswith("#"):
            yield number, line


def subject_findings(source: str, subject: str, limit: int, kind: str) -> list[str]:
    """Return the findings for a commit subject or a pull request title."""
    findings: list[str] = []
    if len(subject) > limit:
        findings.append(f"{source}: the {kind} is longer than {limit} characters")
    if subject.rstrip().endswith("."):
        findings.append(f"{source}: the {kind} ends with a full stop")
    if not subject[:1].isupper():
        findings.append(f"{source}: the {kind} does not start with a capital letter")
    return findings


def check_subject(source: str, message: str, *, commit_message: bool = False) -> list[str]:
    """Return the findings for the subject line of a commit message."""
    lines = message_lines(message) if commit_message else enumerate(message.splitlines(), start=1)
    number, subject = next(((n, line) for n, line in lines if line.strip()), (1, ""))
    return subject_findings(f"{source}:{number}", subject, MAX_SUBJECT, "subject")


def check_title(source: str, title: str) -> list[str]:
    """Return the findings for a pull request title, which becomes a commit subject."""
    return [*subject_findings(source, title, MAX_TITLE, "title"), *check_text(source, title)]


def check_text(source: str, text: str, *, commit_message: bool = False) -> list[str]:
    """Return the findings for text: trailers and personal data.

    ``commit_message`` skips the lines that git leaves out of a message. Findings name
    the line and the rule, never the matched text, so a failing CI run does not repeat
    it in a public log.
    """
    lines = message_lines(text) if commit_message else enumerate(text.splitlines(), start=1)
    findings: list[str] = []
    for number, line in lines:
        if TRAILER.search(line):
            findings.append(f"{source}:{number}: trailer")
        findings.extend(f"{source}:{number}: {kind}" for kind in personal_data_in(line))
    return findings


def check_branch(name: str, *, allow_main: bool = False) -> list[str]:
    """Return the findings for a branch name. Dependabot's branches are not checked."""
    if name.startswith(DEPENDABOT_BRANCH) or (allow_main and name == MAIN):
        return []
    if BRANCH.match(name):
        return []
    return ["branch: the name does not follow <issue-number>-<short-slug>"]


def commits_in(revisions: list[str]) -> list[Commit]:
    """Return the commits that ``git log`` lists for the revision arguments."""
    result = subprocess.run(
        ["git", "log", f"--format=%H{FIELD}%an <%ae>{FIELD}%cn <%ce>{FIELD}%B{RECORD}", *revisions],
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[Commit] = []
    for record in result.stdout.split(RECORD):
        entry = record.strip("\n")
        if entry:
            sha, author, committer, message = entry.split(FIELD, 3)
            commits.append(Commit(sha, author, committer, message))
    return commits


def check_identity(source: str, role: str, identity: str) -> list[str]:
    """Return the findings for one ``Name <email>`` identity, without the address."""
    match = IDENT.match(identity)
    if match is None:
        return [f"{source}: the {role} has no email address"]
    if NOREPLY.search(match["email"]):
        return []
    return [
        f"{source}: the {role} ({match['name']}) uses an address that is not a GitHub "
        "noreply address"
    ]


def current_identity() -> list[str]:
    """Return the findings for the author and committer git would record now."""
    findings: list[str] = []
    for role, variable in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
        result = subprocess.run(
            ["git", "var", variable], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            findings.append(f"git: the {role} identity is not configured")
        else:
            findings.extend(check_identity("git", role, result.stdout.strip()))
    return findings


def check_commit(commit: Commit) -> list[str]:
    """Return the findings for one commit. Dependabot's commits are not checked."""
    if commit.author.startswith(DEPENDABOT):
        return []
    short = commit.sha[:12]
    return [
        *check_identity(short, "author", commit.author),
        *check_identity(short, "committer", commit.committer),
        *check_subject(short, commit.message),
        *check_text(short, commit.message),
    ]


def pushed_findings() -> list[str]:
    """Return the findings for a push, from the variables that pre-commit sets."""
    remote = os.environ.get("PRE_COMMIT_REMOTE_NAME") or "origin"
    findings: list[str] = []
    remote_ref = os.environ.get("PRE_COMMIT_REMOTE_BRANCH", "")
    if remote_ref.startswith("refs/heads/"):
        findings.extend(check_branch(remote_ref.removeprefix("refs/heads/"), allow_main=True))
    revisions = ["--branches", "--not", f"--remotes={remote}"]
    to_ref = os.environ.get("PRE_COMMIT_TO_REF")
    if to_ref:
        revisions.insert(0, to_ref)
    for commit in commits_in(revisions):
        findings.extend(check_commit(commit))
    return findings


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check commits, pull request text and branch names before they become public."
    )
    parser.add_argument("files", nargs="*", type=Path, help="commit message files")
    parser.add_argument(
        "--identity",
        action="store_true",
        help="check the author and committer that git would record now",
    )
    parser.add_argument(
        "--pre-push",
        action="store_true",
        help="check a push, from the variables that pre-commit sets",
    )
    parser.add_argument(
        "--range",
        dest="ranges",
        action="append",
        default=[],
        metavar="RANGE",
        help="git revision range whose commits to check",
    )
    parser.add_argument(
        "--title-env",
        dest="title_names",
        action="append",
        default=[],
        metavar="NAME",
        help="environment variable that holds a pull request title",
    )
    parser.add_argument(
        "--env",
        dest="names",
        action="append",
        default=[],
        metavar="NAME",
        help="environment variable that holds text to check",
    )
    parser.add_argument(
        "--branch",
        dest="branches",
        action="append",
        default=[],
        metavar="NAME",
        help="branch name to check",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    options = parse_arguments(arguments)
    files: list[Path] = options.files
    identity: bool = options.identity
    pre_push: bool = options.pre_push
    ranges: list[str] = options.ranges
    title_names: list[str] = options.title_names
    names: list[str] = options.names
    branches: list[str] = options.branches
    if not (files or identity or pre_push or ranges or title_names or names or branches):
        print(
            "Nothing to check: pass a message file, --identity, --pre-push, --range, "
            "--title-env, --env or --branch.",
            file=sys.stderr,
        )
        return 2
    findings: list[str] = current_identity() if identity else []
    if pre_push:
        findings.extend(pushed_findings())
    for path in files:
        text = path.read_text(encoding="utf-8")
        findings.extend(check_subject(str(path), text, commit_message=True))
        findings.extend(check_text(str(path), text, commit_message=True))
    for revision_range in ranges:
        for commit in commits_in([revision_range]):
            findings.extend(check_commit(commit))
    for name in title_names:
        findings.extend(check_title(name, os.environ.get(name, "")))
    for name in names:
        findings.extend(check_text(name, os.environ.get(name, "")))
    for branch in branches:
        findings.extend(check_branch(branch))
    for finding in findings:
        print(finding)
    if findings:
        print(
            "Commits use a GitHub noreply address and plain messages, branches are named "
            "after their issue, and nothing contains personal data. See CONTRIBUTING.md.",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
