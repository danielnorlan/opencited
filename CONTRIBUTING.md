# Contributing

Thank you for your interest in OpenCited.

## Issues are welcome

OpenCited is maintained by one person. To keep the copyright in one place while the
project takes shape, pull requests from outside contributors are not accepted for
now. Bug reports, questions and ideas are welcome as
[issues](https://github.com/danielnorlan/opencited/issues).
Security problems go through the [security policy](SECURITY.md).

## Development setup

You need [uv](https://docs.astral.sh/uv/), `make` and `git`.

```sh
git clone https://github.com/danielnorlan/opencited.git
cd opencited
make setup   # create the environment and install git hooks
make check   # run the checks that CI runs, on one Python version
```

## How work is organised

Work starts as an issue, and larger work as a specification in `docs/specs/`.
[Development](docs/development.md) describes the flow from issue to merge and the
checks that run on every change.

## Writing style

Text in the repository is English with British spelling. Write short, concrete
sentences and describe only what exists.

## Commits and pull requests

The history of `main` is the public record of the project, so it is written for
readers years from now.

- Pull requests are squash-merged, and the pull request title becomes the commit
  subject on `main`. The title follows the rules for a commit subject, with at most
  64 characters, because GitHub appends the pull request number.
- Write the subject in the imperative mood and in sentence case, with no full stop
  and at most 72 characters, for example `Add table search tool`.
- When a commit has a body, use it to explain why the change was made. The diff
  shows what changed.
- Keep one logical change in each commit and each pull request.
- Name the branch after its issue, as `<issue-number>-<short-slug>`, for example
  `12-table-search`.
- Each commit has one author, the person who wrote it, and commit messages carry no
  trailers such as `Co-authored-by`.
- Files, commit messages and pull requests contain no personal data: no personal
  email address, no path in a home directory and no machine name.
- Commit with your GitHub noreply address as author and committer, so no personal
  address enters the history. Set it in the clone with
  `git config user.email <id>+<login>@users.noreply.github.com`. `make setup` checks it.
- Open pull requests with
  `python scripts/pull_request.py open --title <title> --body-file <file>` and merge
  them with `python scripts/pull_request.py merge <number>`, after a run with
  `--dry-run`. The script checks the pull request and CI, squash-merges with the
  title and the pull request number as the whole commit message and your noreply
  address as author, and verifies the commit that lands on `main`. GitHub
  takes the author name of a squash commit from your profile, so the profile name
  must match your git author name, and it accepts the noreply address only when your
  account keeps its email addresses private (Settings, Emails).

`scripts/check_commit_message.py` and `scripts/check_personal_data.py` enforce these
rules. They run in the git hooks that `make setup` installs, on each commit, each
commit message and each push, and again in CI. A commit that reaches GitHub stays in
the history for good, so the checks run before anything leaves your machine.
