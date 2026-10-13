"""Tests for the checks that open and merge pull requests. None of them calls GitHub."""

from __future__ import annotations

from dataclasses import replace

import pull_request as flow
import pytest
from check_commit_message import Commit

OWNER = "maintainer"
AUTHOR = flow.Identity("Kari Nordmann", "1+kari@users.noreply.github.com")
OLD_TIP = "a" * 40
HEADLINE = "Add table search (#12)"
READY = flow.PullRequest(
    number=12,
    node_id="PR_1",
    title="Add table search",
    body="## Summary\n\nAdds table search.\n",
    state="open",
    draft=False,
    base="main",
    head_ref="12-table-search",
    head_sha="b" * 40,
    author=OWNER,
    mergeable=True,
)
PASSED = [
    flow.Check(name, "github-actions", "completed", "success") for name in flow.REQUIRED_CHECKS
]
MERGED = Commit("c" * 40, str(AUTHOR), flow.GITHUB_COMMITTER, f"{HEADLINE}\n")


def test_accepts_a_ready_pull_request() -> None:
    assert flow.pull_request_findings(READY, OWNER) == []


@pytest.mark.parametrize(
    "changed",
    [
        replace(READY, state="closed"),
        replace(READY, draft=True),
        replace(READY, base="develop"),
        replace(READY, mergeable=None),
        replace(READY, mergeable=False),
        replace(READY, author="someone-else"),
        replace(READY, title="add table search"),
        replace(READY, title="Add table search."),
        replace(READY, title="A" * 65),
        replace(READY, body="Co-authored-by: Kari Nordmann <kari@example.com>"),
        replace(READY, body="Recorded under /home/kari/projects."),
        replace(READY, head_ref="feature/12-table-search"),
    ],
)
def test_rejects_a_pull_request_that_is_not_ready(changed: flow.PullRequest) -> None:
    assert flow.pull_request_findings(changed, OWNER)


def test_applies_only_state_rules_to_dependabot() -> None:
    bump = replace(
        READY,
        author="dependabot[bot]",
        title="bump astral-sh/setup-uv from 10.3.0 to 10.4.0 in the actions group",
        head_ref="dependabot/github_actions/actions-0123456789",
    )
    assert flow.pull_request_findings(bump, OWNER) == []


def test_accepts_passed_checks() -> None:
    assert flow.check_findings(PASSED) == []


@pytest.mark.parametrize(
    ("checks", "finding"),
    [
        (PASSED[1:], f"checks: '{flow.REQUIRED_CHECKS[0]}' has not run"),
        (
            [replace(PASSED[0], conclusion="failure"), *PASSED[1:]],
            f"checks: '{flow.REQUIRED_CHECKS[0]}' ended with failure",
        ),
        (
            [replace(PASSED[0], status="in_progress", conclusion=""), *PASSED[1:]],
            f"checks: '{flow.REQUIRED_CHECKS[0]}' has not finished",
        ),
        (
            [*PASSED, flow.Check("External build", "external-ci", "completed", "failure")],
            "checks: 'External build' ended with failure",
        ),
    ],
)
def test_rejects_checks_that_did_not_pass(checks: list[flow.Check], finding: str) -> None:
    assert flow.check_findings(checks) == [finding]


def test_accepts_the_expected_identity() -> None:
    assert flow.identity_findings(AUTHOR, AUTHOR.name, OWNER, OWNER) == []


def test_rejects_a_profile_name_that_differs_from_the_git_author() -> None:
    findings = flow.identity_findings(AUTHOR, "kari", OWNER, OWNER)
    assert len(findings) == 1
    assert findings[0].startswith("GitHub: the profile name (kari) differs")


def test_rejects_an_author_without_a_noreply_address() -> None:
    author = flow.Identity("Kari Nordmann", "kari@example.com")
    assert flow.identity_findings(author, author.name, OWNER, OWNER) == [
        "git: user.email is not a GitHub noreply address"
    ]


def test_rejects_another_logged_in_account() -> None:
    assert flow.identity_findings(AUTHOR, AUTHOR.name, "second-account", OWNER)


def test_accepts_commits_by_the_squash_author() -> None:
    commits = [Commit("e" * 40, str(AUTHOR), str(AUTHOR), "Add table search\n")]
    assert flow.commit_author_findings(commits, AUTHOR) == []


def test_rejects_commits_by_another_author() -> None:
    other = "Kari Nordmann <2+kari@users.noreply.github.com>"
    commits = [Commit("e" * 40, other, other, "Add table search\n")]
    assert flow.commit_author_findings(commits, AUTHOR) == [
        f"{'e' * 12}: the author differs from the author of the squash commit"
    ]


def test_accepts_the_expected_merge_commit() -> None:
    findings = flow.merge_commit_findings(
        MERGED, OLD_TIP, old_tip=OLD_TIP, headline=HEADLINE, author=AUTHOR
    )
    assert findings == []


@pytest.mark.parametrize(
    ("commit", "parent", "finding"),
    [
        (
            replace(MERGED, message=f"{HEADLINE}\n\nSee the pull request for details.\n"),
            OLD_TIP,
            "main: the merge commit message is not the title alone",
        ),
        (
            replace(MERGED, author="kari <2+kari@users.noreply.github.com>"),
            OLD_TIP,
            "main: the merge commit has an unexpected author",
        ),
        (
            replace(MERGED, committer=str(AUTHOR)),
            OLD_TIP,
            "main: the merge commit has an unexpected committer",
        ),
        (MERGED, "d" * 40, "main: the merge commit does not follow the previous tip of main"),
    ],
)
def test_rejects_an_unexpected_merge_commit(commit: Commit, parent: str, finding: str) -> None:
    findings = flow.merge_commit_findings(
        commit, parent, old_tip=OLD_TIP, headline=HEADLINE, author=AUTHOR
    )
    assert findings == [finding]


@pytest.mark.parametrize(
    ("login", "normalised"),
    [
        ("dependabot[bot]", "dependabot"),
        ("app/dependabot", "dependabot"),
        ("Maintainer", "maintainer"),
    ],
)
def test_normalises_logins(login: str, normalised: str) -> None:
    assert flow.normalise(login) == normalised


def test_rejects_a_co_author_trailer_on_the_merge_commit() -> None:
    commit = replace(MERGED, message=f"{HEADLINE}\n\nCo-authored-by: {AUTHOR}\n")
    findings = flow.merge_commit_findings(
        commit, OLD_TIP, old_tip=OLD_TIP, headline=HEADLINE, author=AUTHOR
    )
    assert findings == [
        f"{'c' * 12}:3: trailer",
        "main: the merge commit message is not the title alone",
    ]
