# Changelog

Notable changes to OpenCited are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project foundation: licence, CI, pre-commit hooks, decision records 0001 to 0006
  and specification 0001.
- The plan for M1 and M2, specifications 0002 to 0005 and decision records 0007 to
  0012.
- `scripts/record_ssb_fixtures.py` and the first responses recorded from SSB's API, in
  `tests/fixtures/ssb/`.
- The StatBank client in `opencited.sources.ssb`, which searches tables, reads their
  metadata and fetches data into typed models with exact decimals, and a replay
  transport that serves recorded responses to tests.
- Query results with a provenance record for every result, and every symbol that
  replaces a missing value, with SSB's explanation where SSB gives one. Decimals are
  written in fixed point in JSON and never read from a binary number.
- Queries check their selection against the table's metadata, count its cells and
  refuse more than 5,000 before any data request, and fetch no data from 05:00 to
  08:15 Norwegian time, when SSB shows figures under revision.
- Checks for commits, branch names and personal data, run in the git hooks and in CI
  ([ADR 0006](docs/adr/0006-commit-identity-and-personal-data.md)).
- `scripts/pull_request.py`, which opens pull requests, squash-merges them once CI
  passes, and verifies the commit on `main`.
- `scripts/bootstrap_github.sh`, which sets up the labels, the milestones, squash
  merges and the GitHub Actions that workflows may use.
