#!/usr/bin/env python3
"""Open and merge pull requests the way the history of main is recorded.

Usage:
    python scripts/pull_request.py open --title TITLE --body-file FILE
    python scripts/pull_request.py merge NUMBER [--dry-run]

`open` checks the branch name, the commits the branch adds, the title and the
description, and opens the pull request only when every check passes. The branch must
be pushed first, so the pre-push hook has checked it.

`merge` checks the pull request again: its state, its commits and CI. It then
squash-merges with the title and the pull request number as the whole commit message
and your noreply address as author, and verifies the commit that lands on main. A
merge with GitHub's defaults can put a personal address or a co-author trailer into
the history, and nothing can be removed from the history afterwards (ADR 0006).
GitHub takes the author name of the squash commit from your profile, so the profile
name must match the git author name. --dry-run runs every check without merging.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from check_commit_message import (
    IDENT,
    NOREPLY,
    Commit,
    check_branch,
    check_commit,
    check_text,
    check_title,
    commits_in,
)

BASE = "main"
# The jobs in .github/workflows/. A pull request merges only when each has passed.
REQUIRED_CHECKS = (
    "Pre-commit hooks",
    "Types and tests (Python 3.13)",
    "Types and tests (Python 3.14)",
    "Build the package",
    "Commits and branch name",
)
PASSING = frozenset({"success", "neutral", "skipped"})
GITHUB_COMMITTER = "GitHub <noreply@github.com>"
INVALID_EMAIL = "Invalid email address"
MERGEABLE_ATTEMPTS = 10
MERGEABLE_WAIT_SECONDS = 3
MERGE_MUTATION = """
mutation($id: ID!, $oid: GitObjectID!, $headline: String!, $body: String!, $email: String!) {
  mergePullRequest(input: {
    pullRequestId: $id, expectedHeadOid: $oid, mergeMethod: SQUASH,
    commitHeadline: $headline, commitBody: $body, authorEmail: $email
  }) {
    pullRequest { mergeCommit { oid } }
  }
}
"""


@dataclass(frozen=True)
class PullRequest:
    number: int
    node_id: str
    title: str
    body: str
    state: str
    draft: bool
    base: str
    head_ref: str
    head_sha: str
    author: str
    mergeable: bool | None


@dataclass(frozen=True)
class Check:
    name: str
    app: str
    status: str
    conclusion: str


@dataclass(frozen=True)
class Identity:
    name: str
    email: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


def run(command: Sequence[str], *, stdin: str | None = None) -> str:
    """Run a command and return its output, or stop with its error."""
    result = subprocess.run(list(command), input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"`{' '.join(command[:3])}` failed: {result.stderr.strip()}")
    return result.stdout


def as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SystemExit("Unexpected response from GitHub: expected an object.")
    return cast("dict[str, object]", value)


def as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise SystemExit("Unexpected response from GitHub: expected a list.")
    return cast("list[object]", value)


def dict_or_empty(value: object) -> dict[str, object]:
    """Return an optional object from a response, or an empty one when it is missing."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def login_of(item: dict[str, object], key: str = "user") -> str:
    return text(dict_or_empty(item.get(key)).get("login"))


def api(path: str) -> object:
    return json.loads(run(["gh", "api", path]))


def api_pages(path: str) -> list[object]:
    """Return every page of a paginated endpoint, one list item per page."""
    return as_list(json.loads(run(["gh", "api", "--paginate", "--slurp", path])))


def api_items(path: str) -> list[dict[str, object]]:
    """Return the items of a paginated endpoint that answers with lists."""
    return [as_dict(item) for page in api_pages(path) for item in as_list(page)]


def normalise(login: str) -> str:
    """Return a login without GitHub's app prefix or bot suffix, in lower case."""
    return login.removeprefix("app/").removesuffix("[bot]").lower()


def check_findings(checks: Sequence[Check], required: Sequence[str] = REQUIRED_CHECKS) -> list[str]:
    """Return the findings for the checks on the head commit of a pull request."""
    names = {check.name for check in checks}
    findings = [f"checks: '{name}' has not run" for name in required if name not in names]
    for check in checks:
        if check.status != "completed":
            findings.append(f"checks: '{check.name}' has not finished")
        elif check.conclusion not in PASSING:
            findings.append(f"checks: '{check.name}' ended with {check.conclusion or 'no result'}")
    return findings


def pull_request_findings(pr: PullRequest, owner: str) -> list[str]:
    """Return the findings for the state and text of a pull request."""
    findings: list[str] = []
    if pr.state != "open":
        findings.append(f"pull request: the state is {pr.state}")
    if pr.draft:
        findings.append("pull request: it is a draft")
    if pr.base != BASE:
        findings.append(f"pull request: the base is {pr.base}, and it must be {BASE}")
    if pr.mergeable is not True:
        findings.append("pull request: GitHub does not report it as mergeable")
    if normalise(pr.author) == "dependabot":
        return findings
    if normalise(pr.author) != normalise(owner):
        findings.append(f"pull request: opened by {pr.author or 'an unknown account'}")
    findings.extend(check_title("title", pr.title))
    findings.extend(check_text("description", pr.body))
    findings.extend(check_branch(pr.head_ref))
    return findings


def identity_findings(author: Identity, profile_name: str, login: str, owner: str) -> list[str]:
    """Return the findings for the identity that the squash commit will carry."""
    findings: list[str] = []
    if not NOREPLY.search(author.email):
        findings.append("git: user.email is not a GitHub noreply address")
    if profile_name != author.name:
        findings.append(
            f"GitHub: the profile name ({profile_name or 'empty'}) differs from the git "
            f"author name ({author.name}). GitHub uses the profile name as the author of "
            "the squash commit. Change it at https://github.com/settings/profile"
        )
    if normalise(login) != normalise(owner):
        findings.append(f"GitHub: the CLI is logged in as {login}, and {owner} owns the repository")
    return findings


def commit_author_findings(commits: Sequence[Commit], author: Identity) -> list[str]:
    """Return a finding for each commit whose author differs from the squash author.

    GitHub adds a co-author trailer for every other author when it writes the default
    squash message, so every commit must carry exactly the identity of the merge.
    """
    return [
        f"{commit.sha[:12]}: the author differs from the author of the squash commit"
        for commit in commits
        if commit.author != str(author)
    ]


def merge_commit_findings(
    commit: Commit, parent: str, *, old_tip: str, headline: str, author: Identity
) -> list[str]:
    """Return the findings for the commit that a merge put on main."""
    findings = check_commit(commit)
    if parent != old_tip:
        findings.append("main: the merge commit does not follow the previous tip of main")
    if commit.author != str(author):
        findings.append("main: the merge commit has an unexpected author")
    if commit.committer != GITHUB_COMMITTER:
        findings.append("main: the merge commit has an unexpected committer")
    if commit.message.strip() != headline:
        findings.append("main: the merge commit message is not the title alone")
    return findings


def current_repo() -> str:
    return run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()


def git_author() -> Identity:
    """Return the author identity that git uses in this clone."""
    match = IDENT.match(run(["git", "var", "GIT_AUTHOR_IDENT"]).strip())
    if match is None:
        raise SystemExit("git: the author identity is not configured.")
    return Identity(match["name"], match["email"])


def fetch_pull_request(repo: str, number: int) -> PullRequest:
    """Return a pull request, waiting while GitHub still computes whether it merges."""
    data = as_dict(api(f"repos/{repo}/pulls/{number}"))
    for _ in range(MERGEABLE_ATTEMPTS):
        if isinstance(data.get("mergeable"), bool):
            break
        time.sleep(MERGEABLE_WAIT_SECONDS)
        data = as_dict(api(f"repos/{repo}/pulls/{number}"))
    mergeable = data.get("mergeable")
    head = as_dict(data.get("head"))
    return PullRequest(
        number=number,
        node_id=text(data.get("node_id")),
        title=text(data.get("title")),
        body=text(data.get("body")),
        state=text(data.get("state")),
        draft=data.get("draft") is True,
        base=text(as_dict(data.get("base")).get("ref")),
        head_ref=text(head.get("ref")),
        head_sha=text(head.get("sha")),
        author=login_of(data),
        mergeable=mergeable if isinstance(mergeable, bool) else None,
    )


def fetch_checks(repo: str, sha: str) -> list[Check]:
    """Return the check runs and commit statuses of a commit."""
    checks: list[Check] = []
    for page in api_pages(f"repos/{repo}/commits/{sha}/check-runs"):
        for entry in as_list(as_dict(page).get("check_runs", [])):
            item = as_dict(entry)
            checks.append(
                Check(
                    name=text(item.get("name")),
                    app=text(dict_or_empty(item.get("app")).get("slug")),
                    status=text(item.get("status")),
                    conclusion=text(item.get("conclusion")),
                )
            )
    for entry in as_list(as_dict(api(f"repos/{repo}/commits/{sha}/status")).get("statuses", [])):
        item = as_dict(entry)
        state = text(item.get("state"))
        checks.append(
            Check(
                name=text(item.get("context")),
                app="a commit status",
                status="in_progress" if state == "pending" else "completed",
                conclusion=state,
            )
        )
    return checks


def report(findings: Sequence[str], summary: str) -> int:
    for finding in findings:
        print(finding)
    print(summary, file=sys.stderr)
    return 1


def open_pull_request(title: str, body_file: Path) -> int:
    body = body_file.read_text(encoding="utf-8")
    branch = run(["git", "branch", "--show-current"]).strip()
    findings = [
        *check_branch(branch),
        *check_title("title", title),
        *check_text("description", body),
    ]
    run(["git", "fetch", "--quiet", "origin"])
    head = run(["git", "rev-parse", "HEAD"]).strip()
    pushed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if pushed != head:
        findings.append("branch: push it first, so the pre-push hook checks it")
    commits = commits_in([f"origin/{BASE}..HEAD"])
    if not commits:
        findings.append(f"branch: it has no commits that {BASE} does not have")
    for commit in commits:
        findings.extend(check_commit(commit))
    if findings:
        return report(findings, "The pull request was not opened.")
    print(
        run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                BASE,
                "--head",
                branch,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ]
        ).strip()
    )
    return 0


def merge_pull_request(number: int, *, dry_run: bool) -> int:
    repo = current_repo()
    owner = repo.split("/")[0]
    author = git_author()
    profile = as_dict(api("user"))
    findings = identity_findings(
        author, text(profile.get("name")), text(profile.get("login")), owner
    )
    pr = fetch_pull_request(repo, number)
    findings.extend(pull_request_findings(pr, owner))
    run(["git", "fetch", "--quiet", "origin", BASE, f"refs/pull/{number}/head"])
    old_tip = run(["git", "rev-parse", f"origin/{BASE}"]).strip()
    commits = commits_in([f"{old_tip}..{pr.head_sha}"])
    if not commits:
        findings.append(f"pull request: it has no commits that {BASE} does not have")
    for commit in commits:
        findings.extend(check_commit(commit))
    if normalise(pr.author) != "dependabot":
        findings.extend(commit_author_findings(commits, author))
    findings.extend(check_findings(fetch_checks(repo, pr.head_sha)))
    if findings:
        return report(findings, f"Pull request #{number} was not merged.")
    headline = f"{pr.title} (#{number})"
    if dry_run:
        print(f"Every check passes. #{number} would merge as: {headline}")
        return 0
    payload = {
        "query": MERGE_MUTATION,
        "variables": {
            "id": pr.node_id,
            "oid": pr.head_sha,
            "headline": headline,
            "body": "",
            "email": author.email,
        },
    }
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.strip()
        if INVALID_EMAIL in error:
            error += (
                ". GitHub accepts the noreply address as the author of a merge only when "
                "the account keeps its email addresses private: turn on 'Keep my email "
                "addresses private' at https://github.com/settings/emails"
            )
        return report([f"merge: {error}"], f"Pull request #{number} was not merged.")
    run(["git", "fetch", "--quiet", "origin", BASE])
    new_tip = run(["git", "rev-parse", f"origin/{BASE}"]).strip()
    merged = commits_in([f"{old_tip}..{new_tip}"])
    if len(merged) != 1:
        return report(
            [f"main: {len(merged)} new commits after the merge, and there should be one"],
            "Check main by hand.",
        )
    parent = run(["git", "rev-parse", f"{new_tip}^"]).strip()
    problems = merge_commit_findings(
        merged[0], parent, old_tip=old_tip, headline=headline, author=author
    )
    if problems:
        return report(problems, "The merge commit on main needs attention.")
    print(f"Merged #{number} as {new_tip[:12]}: {headline}")
    return 0


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open and merge pull requests the way the history of main is recorded."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    opener = commands.add_parser("open", help="check the branch and open a pull request")
    opener.add_argument("--title", required=True)
    opener.add_argument("--body-file", type=Path, required=True)
    merger = commands.add_parser("merge", help="check a pull request and squash-merge it")
    merger.add_argument("number", type=int)
    merger.add_argument("--dry-run", action="store_true", help="check without merging")
    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    options = parse_arguments(arguments)
    command: str = options.command
    if command == "open":
        title: str = options.title
        body_file: Path = options.body_file
        return open_pull_request(title, body_file)
    number: int = options.number
    dry_run: bool = options.dry_run
    return merge_pull_request(number, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
