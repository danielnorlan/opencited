# Development

This page describes how a change moves from an issue to `main` and which checks it
passes on the way.

## From issue to merge

1. Work starts as an issue. Larger work starts as a specification in `docs/specs/`.
2. Each issue gets one branch, named `<issue-number>-<short-slug>`, and one pull
   request.
3. CI runs the same checks as `make check`, on Python 3.13 and 3.14, and checks the
   commits in the pull request.
4. Pull requests are squash-merged through `scripts/pull_request.py`, so the commit on
   `main` carries the pull request title and number. The pull request keeps the
   record of the change.

## Checks

| Check | Where | What it stops |
| --- | --- | --- |
| pre-commit | Locally and in CI | Formatting and lint errors, leaked secrets (gitleaks), unsafe workflows (zizmor) and invalid configuration files |
| Personal data check | pre-commit and CI | Personal email addresses, paths in home directories and machine names in files |
| Commit check | `commit-msg` and `pre-push` hooks, and CI | Authors and committers without a GitHub noreply address; trailers and personal data in commit messages; subjects longer than 72 characters, with a full stop or without a capital letter; branch names outside `<issue-number>-<short-slug>` |
| Pyright and pytest | Locally and in CI | Type errors and failing tests |
| Live smoke test | `uv run pytest -m live` locally, and the `Nightly` workflow | Nothing: it reports changes in SSB's API that the recorded fixtures cannot show |
| Model endpoint test | `uv run pytest -m model` locally, with the `OPENCITED_MODEL_*` variables set | Nothing: it shows that the configured endpoint takes the tool schemas and reports usage |
| Evaluation suite job | CI, and `uv run --group evals pytest evals/tests` locally | Type errors and failing tests in `evals/`, with Inspect installed |
| Package build | Locally and in CI | A wheel that does not build or does not import, release metadata that disagrees between `pyproject.toml`, `server.json` and the README, and a server that does not start from the wheel in a clean environment |
| Merge script | `scripts/pull_request.py` | Merging while CI fails; a squash commit with an unexpected author, committer or message |

The commit check runs before anything leaves the machine, because nothing can be
removed from the history once it reaches GitHub.
[ADR 0006](adr/0006-commit-identity-and-personal-data.md) records the decision.

## Repository settings

`scripts/bootstrap_github.sh` creates the labels and milestones, allows only squash
merges with the pull request title as the commit message, and turns off the wiki,
projects and discussions. Workflows may use only the actions that the script lists,
pinned to a full commit SHA, with a read-only token that cannot approve pull
requests.
