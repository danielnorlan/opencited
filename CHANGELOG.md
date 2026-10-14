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
- The gate passes a date that a cited query result gives as the day of its figures,
  such as "1. januar 2026" for a population at "Befolkning 1. januar" in 2026, a change
  to spec 0003 that the maintainer approved. Version 3 of the system prompt says so,
  and says that a calculation backs only the numbers it computes, so a period or a
  date beside them needs its source result cited as well. A blocked reply's hint for a
  date or a period says to cite that result or leave it out, and a four-digit number
  that matched nothing gets the hints for a year and for a value.
- The model reads each dimension's values as a map from code to label, which halves
  the metadata of large tables, and a blocked reply goes back with what to do about
  each kind of failed number. Version 2 of the system prompt asks for changes with
  their sign and periods as the results name them.
- `OPENCITED_MODEL_EXTRA`, a JSON object of extra request fields that the model client
  sends and every answer records, such as the field that turns off a model's thinking
  mode.
- The evaluation suite as an Inspect task in `evals/`, with the agent behind Inspect's
  agent bridge and only its own four tools, Inspect's limits above the agent's, runs
  kept away from SSB's update periods, and `evals/report.py`, which writes the run
  report from the log (spec 0005). Inspect 0.3.279 is pinned in the `evals` group, and
  a CI job type-checks and tests the task.
- The first question set, `evals/questions/v1.jsonl`: 44 questions in Norwegian and
  English, in the eight categories of spec 0005, with reference plans whose expected
  numbers a run computes from the day's data; the runner of those plans; and
  recordings of every table and selection they read, made with
  `scripts/record_ssb_fixtures.py --questions`.
- The evaluation suite's scorer, which decides with the gate's rules whether an answer
  holds the expected numbers at the required decimals and a ranking answer its label,
  and its report of correct answers by category and language, wrong refusals and
  answers, gate blocks, retries and use (spec 0005). Answers and refusals record what
  became of each reply the gate checked.
- `opencited ask`, which answers a question with the configured model and prints the
  answer with numbered sources, or with `--json` every record of it (spec 0004). The
  agent's loop sends a versioned system prompt, runs the tools, checks the reply with
  the evidence gate, retries a blocked reply at most twice, and stops at its limits
  of model calls, tool calls, tokens, time and cost, deciding each step with a plain
  function of the run state.
- The gate report lists, for each matched number, every value of the cited results
  that it fits, with the cell or the calculation step.
- The agent's model client and tools (spec 0004): the endpoint, the model, its
  settings, prices and limits from `OPENCITED_*` variables; a client for chat
  completions that keeps the API key out of its representation, its errors and its
  logs; and a toolbox that calls the MCP tools and `calculate`, runs only calls that match
  the published schemas and gives every result an evidence ID.
- The evidence gate, which matches every number of an answer to the results its
  block cites and reports each number's match or the reason it failed, with a corpus
  of example answers over recorded data (spec 0003).
- A reader of the numbers in an answer, in Norwegian and English, with dates,
  periods, ranges, percent and scale words and the forms that block an answer, and a
  formatter for decimals in both languages (spec 0003).
- The calculation engine: typed plans over snapshots, checked before they run, with
  DuckDB locked to the loaded snapshots and exact rational arithmetic (spec 0002).
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
