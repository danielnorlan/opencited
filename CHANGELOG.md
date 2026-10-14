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
- The MCP server, with the tools `search_tables`, `get_table_metadata` and
  `query_table`, and the command `opencited mcp`, which serves them over stdio or,
  with `--http`, over Streamable HTTP on a loopback address. Search and metadata
  results link to the table in StatBank.
- A response cache in SQLite for metadata and data, served until the next 08:15
  Norwegian time, and a rate limiter that keeps each process within 30 requests a
  minute and backs off after HTTP 429. `opencited mcp` takes `--cache-dir` and
  `--no-cache`.
- Write-once snapshots of query results, with the cells in Parquet as exact decimals,
  kept by `opencited mcp --snapshot-dir` (spec 0002). ADR 0011 is accepted.
- A release workflow that publishes to PyPI with trusted publishing when a GitHub
  release is published, `server.json` for the MCP Registry, a check that the release
  metadata agrees, a check in CI that starts the server from the built wheel, client
  setup in the README and the maintainer's steps in `docs/releasing.md`.
- A live smoke test that calls every tool through `opencited mcp` against SSB's API,
  run every night by the `Nightly` workflow.
- Checks for commits, branch names and personal data, run in the git hooks and in CI
  ([ADR 0006](docs/adr/0006-commit-identity-and-personal-data.md)).
- `scripts/pull_request.py`, which opens pull requests, squash-merges them once CI
  passes, and verifies the commit on `main`.
- `scripts/bootstrap_github.sh`, which sets up the labels, the milestones, squash
  merges and the GitHub Actions that workflows may use.
