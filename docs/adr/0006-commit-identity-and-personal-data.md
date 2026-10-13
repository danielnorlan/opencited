# 0006. Commit identity and personal data

- Status: Accepted
- Date: 2026-10-13

## Context

The history of `main` and the branch name of every pull request become public with
the repository, and none of it can be removed afterwards. A commit made with a
personal address publishes that address for good. A squash merge with GitHub's
defaults takes the author from the account's profile and primary address, and adds a
co-author line for every other commit author.

## Decision

1. Authors and committers use a GitHub noreply address. Each commit has one author,
   and commit messages carry no trailers. Subjects follow the rules in
   `CONTRIBUTING.md`, and branch names follow `<issue-number>-<short-slug>`.
2. Files, commit messages and pull request text contain no personal data: no
   personal email address, no path in a home directory and no machine name.
3. `scripts/check_commit_message.py` and `scripts/check_personal_data.py` enforce
   these rules in the `commit-msg`, `pre-commit` and `pre-push` hooks that
   `make setup` installs, so they hold before anything leaves the machine, and again
   in CI.
4. Pull requests merge through `scripts/pull_request.py`. It squash-merges with the
   title and the pull request number as the whole commit message and the noreply
   address as author, and verifies the commit on `main`.
5. Workflows may use only the actions that `scripts/bootstrap_github.sh` lists, pinned
   to a full commit SHA, with a read-only token.

## Alternatives considered

- GitHub's merge button with its defaults. It writes the account's primary address
  and co-author lines unless the account and every commit are set up for it.
- Review alone. Identities, trailers and personal data are easy to miss by eye.

## Consequences

- Every clone needs `make setup` once, so the hooks run before the first commit.
- GitHub takes the author name of a squash commit from the profile of the account
  that merges, so the profile name must match the git author name. The merge script
  checks it.
- GitHub accepts the noreply address as the author of a merge only when the account
  keeps its email addresses private. With the setting off, GitHub rejects the merge
  script's request.
- GitHub's merge button bypasses the merge script. It is safe only when the account
  keeps its email addresses private and every commit in the pull request has the same
  author.
