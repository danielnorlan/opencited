"""Tests for the commit check, run as the git hooks and CI run it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_commit_message.py"
TRAILER = "Co-authored-by: Kari Nordmann <kari@example.com>"
DEPENDABOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
NOREPLY = "1+test@users.noreply.github.com"
PERSONAL = "kari@example.com"
NOT_NOREPLY = "uses an address that is not a GitHub noreply address"


def run(
    *arguments: str, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


def check_message(tmp_path: Path, text: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8")
    return run(str(path))


def clean_env(**extra: str) -> dict[str, str]:
    """Return the environment without git identity overrides, plus ``extra``."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(extra)
    return env


def git(
    repo: Path,
    *arguments: str,
    name: str = "Test",
    email: str = NOREPLY,
    env: dict[str, str] | None = None,
) -> None:
    identity = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    subprocess.run(
        ["git", "-C", str(repo), *identity, "-c", "commit.gpgsign=false", *arguments],
        check=True,
        capture_output=True,
        env=env or clean_env(),
    )


def make_repo(path: Path, *, name: str = "Test", email: str = NOREPLY) -> Path:
    """Create a repository on main with one commit, so HEAD~1..HEAD is the next one."""
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(path)], check=True)
    git(
        path,
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "Start the history",
        name=name,
        email=email,
    )
    return path


def make_pushed_repo(tmp_path: Path, *, name: str = "Test", email: str = NOREPLY) -> Path:
    """Create a repository whose main is already on a local remote named origin."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    repo = make_repo(tmp_path / "repo", name=name, email=email)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "origin", "main")
    return repo


def commit_on_branch(repo: Path, branch: str, message: str, *, email: str = NOREPLY) -> None:
    git(repo, "switch", "--quiet", "-c", branch, "main")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", message, email=email)


def pre_push(repo: Path, branch: str) -> subprocess.CompletedProcess[str]:
    """Run the check the way the pre-push hook does for a push of ``branch``."""
    env = clean_env(
        PRE_COMMIT_REMOTE_NAME="origin", PRE_COMMIT_REMOTE_BRANCH=f"refs/heads/{branch}"
    )
    return run("--pre-push", cwd=repo, env=env)


@pytest.mark.parametrize(
    ("text", "finding"),
    [
        (f"Add table search\n\n{TRAILER}\n", "trailer"),
        ("Add table search\n\nSigned-off-by: Kari <1+kari@users.noreply.github.com>\n", "trailer"),
        ("Add table search\n\nCo-authored-by: Kari <kari@gmail.com>\n", "outside the allowed"),
        ("Fix the fixture paths under /home/kari/projects\n", "path in a home directory"),
    ],
)
def test_rejects_messages_that_break_the_rules(tmp_path: Path, text: str, finding: str) -> None:
    result = check_message(tmp_path, text)
    assert result.returncode == 1
    assert finding in result.stdout
    assert "kari" not in result.stdout


@pytest.mark.parametrize(
    "text",
    [
        "Add table search tool\n\nThe search uses the index that the API provides.\n",
        "Record fixtures with the recording script\n",
        "Add table search\n\nReviewed the selection limits against the API guide.\n",
    ],
)
def test_accepts_plain_messages(tmp_path: Path, text: str) -> None:
    assert check_message(tmp_path, text).returncode == 0


def test_reports_the_line_and_rule_without_the_matched_text(tmp_path: Path) -> None:
    result = check_message(tmp_path, f"Add table search\n\n{TRAILER}\n")
    assert result.stdout.splitlines() == [f"{tmp_path / 'COMMIT_EDITMSG'}:3: trailer"]


@pytest.mark.parametrize(
    ("text", "finding"),
    [
        (f"Add {'a' * 69}\n", "the subject is longer than 72 characters"),
        ("Add table search.\n", "the subject ends with a full stop"),
        ("add table search\n", "the subject does not start with a capital letter"),
        ("# A comment git leaves out\nadd table search\n", "2: the subject does not start"),
    ],
)
def test_rejects_subjects_outside_the_rules(tmp_path: Path, text: str, finding: str) -> None:
    result = check_message(tmp_path, text)
    assert result.returncode == 1
    assert finding in result.stdout


def test_accepts_a_subject_of_72_characters(tmp_path: Path) -> None:
    assert check_message(tmp_path, f"Add {'a' * 68}\n").returncode == 0


def test_skips_comments_and_the_diff_of_a_verbose_commit(tmp_path: Path) -> None:
    text = (
        "Add table search\n"
        "# Lines starting with '#' are ignored. The diff follows.\n"
        "# ------------------------ >8 ------------------------\n"
        "+Co-authored-by: Kari Nordmann <kari@example.com>\n"
    )
    assert check_message(tmp_path, text).returncode == 0


def test_checks_text_from_environment_variables() -> None:
    body = f"## Summary\n\nAdds table search.\n\n{TRAILER}\n"
    env = {**os.environ, "PR_TITLE": "Add table search", "PR_BODY": body}
    result = run("--title-env", "PR_TITLE", "--env", "PR_BODY", env=env)
    assert result.returncode == 1
    assert result.stdout.strip() == "PR_BODY:5: trailer"


@pytest.mark.parametrize(
    ("title", "finding"),
    [
        ("A" * 65, "PR_TITLE: the title is longer than 64 characters"),
        ("Add table search.", "PR_TITLE: the title ends with a full stop"),
        ("add table search", "PR_TITLE: the title does not start with a capital letter"),
    ],
)
def test_rejects_titles_outside_the_rules(title: str, finding: str) -> None:
    result = run("--title-env", "PR_TITLE", env={**os.environ, "PR_TITLE": title})
    assert result.returncode == 1
    assert result.stdout.strip() == finding


def test_accepts_a_title_of_64_characters() -> None:
    assert run("--title-env", "PR_TITLE", env={**os.environ, "PR_TITLE": "A" * 64}).returncode == 0


def test_needs_something_to_check() -> None:
    assert run().returncode == 2


@pytest.mark.parametrize(
    "name",
    [
        "feature/12-table-search",
        "fix/12-search",
        "wip-plan",
        "12-Table-Search",
        "0-table-search",
        "12-",
        "main",
    ],
)
def test_rejects_branch_names_outside_the_scheme(name: str) -> None:
    result = run("--branch", name)
    assert result.returncode == 1
    assert result.stdout.startswith("branch: the name ")


@pytest.mark.parametrize(
    "name",
    ["12-table-search", "1-project-foundation", "34-evidence-gate", "dependabot/uv/httpx2-2.14.0"],
)
def test_accepts_branch_names_in_the_scheme(name: str) -> None:
    assert run("--branch", name).returncode == 0


def test_range_accepts_a_clean_history(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "Add table search")
    assert run("--range", "HEAD~1..HEAD", cwd=repo).returncode == 0


def test_range_rejects_a_trailer(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "Add table search", "-m", TRAILER)
    result = run("--range", "HEAD~1..HEAD", cwd=repo)
    assert result.returncode == 1
    assert ":3: trailer" in result.stdout


def test_range_rejects_a_personal_address_without_printing_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(
        repo,
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "Add table search",
        name="Kari Nordmann",
        email=PERSONAL,
    )
    result = run("--range", "HEAD~1..HEAD", cwd=repo)
    assert result.returncode == 1
    assert f"the author (Kari Nordmann) {NOT_NOREPLY}" in result.stdout
    assert PERSONAL not in result.stdout + result.stderr


def test_range_accepts_github_as_the_committer(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    env = clean_env(GIT_COMMITTER_NAME="GitHub", GIT_COMMITTER_EMAIL="noreply@github.com")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "Add table search", env=env)
    assert run("--range", "HEAD~1..HEAD", cwd=repo).returncode == 0


def test_identity_accepts_a_noreply_address(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", NOREPLY)
    assert run("--identity", cwd=repo, env=clean_env()).returncode == 0


def test_identity_rejects_a_personal_address(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(repo, "config", "user.name", "Kari Nordmann")
    git(repo, "config", "user.email", PERSONAL)
    result = run("--identity", cwd=repo, env=clean_env())
    assert result.returncode == 1
    assert f"git: the author (Kari Nordmann) {NOT_NOREPLY}" in result.stdout
    assert PERSONAL not in result.stdout + result.stderr


def test_pre_push_accepts_a_clean_branch(tmp_path: Path) -> None:
    repo = make_pushed_repo(tmp_path)
    commit_on_branch(repo, "12-table-search", "Add table search")
    assert pre_push(repo, "12-table-search").returncode == 0


def test_pre_push_rejects_a_branch_name_outside_the_scheme(tmp_path: Path) -> None:
    repo = make_pushed_repo(tmp_path)
    commit_on_branch(repo, "feature/12-table-search", "Add table search")
    result = pre_push(repo, "feature/12-table-search")
    assert result.returncode == 1
    assert "branch: the name" in result.stdout


def test_pre_push_checks_every_local_branch_the_remote_lacks(tmp_path: Path) -> None:
    repo = make_pushed_repo(tmp_path)
    commit_on_branch(repo, "12-table-search", "Add table search")
    commit_on_branch(repo, "13-metadata", "Add table metadata", email=PERSONAL)
    result = pre_push(repo, "12-table-search")
    assert result.returncode == 1
    assert NOT_NOREPLY in result.stdout


def test_pre_push_ignores_commits_the_remote_already_has(tmp_path: Path) -> None:
    repo = make_pushed_repo(tmp_path, email=PERSONAL)
    commit_on_branch(repo, "12-table-search", "Add table search")
    assert pre_push(repo, "12-table-search").returncode == 0


def test_pre_push_checks_the_whole_history_of_a_new_remote(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    repo = make_repo(tmp_path / "repo", email=PERSONAL)
    git(repo, "remote", "add", "origin", str(remote))
    result = pre_push(repo, "main")
    assert result.returncode == 1
    assert NOT_NOREPLY in result.stdout


def test_pre_push_allows_pushing_main(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    repo = make_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(remote))
    assert pre_push(repo, "main").returncode == 0


def test_range_skips_dependabot_commits(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    name, email = DEPENDABOT
    git(
        repo,
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "Bump the python group",
        "-m",
        "Signed-off-by: dependabot[bot] <support@github.com>",
        name=name,
        email=email,
    )
    assert run("--range", "HEAD~1..HEAD", cwd=repo).returncode == 0
