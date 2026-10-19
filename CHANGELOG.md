# Changelog

Notable changes to OpenCited are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Published runs of question sets v1 and v3 from release 0.1.0 on 2026-10-19: 38 of
  44 and 39 of 43 correct. All 26 answers released in set v1 were correct, and no
  question that should be refused was answered.

## [0.1.0] - 2026-10-19

The first release.

### Added

- An MCP server for Statistics Norway's StatBank, `opencited mcp`, with the tools
  `search_tables`, `get_table_metadata` and `query_table`, over stdio or, with
  `--http`, over Streamable HTTP on a loopback address.
- A provenance record for every query result: the source, the licence, the table, the
  request, the retrieval time and the SHA-256 of the raw response. Missing values keep
  SSB's symbol and its explanation, and values stay exact decimals.
- Queries checked against the table's metadata before any data request: more than
  5,000 cells are refused, and so is a code that SSB's API would read as another code.
  No data is fetched from 05:00 to 08:15 Norwegian time, while SSB revises figures.
- A response cache until the next 08:15 Norwegian time and a rate limiter that keeps
  each process within 30 requests a minute.
- A calculation engine that runs typed plans, such as sums, means, changes, shares and
  rankings, over write-once snapshots of query results, with DuckDB and exact rational
  arithmetic.
- An evidence gate that reads every number of an answer, in Norwegian and English, and
  matches it to a value, a period, a date, a table ID or a label of the results that
  its paragraph, list item or table row cites, or blocks the answer and says why.
  Values in a unit with a multiplier, such as "1000 personer", are compared in their
  base unit, and years, quarters, months and weeks count as periods.
- `opencited ask`, which answers a question with a language model of the user's
  choice, through an endpoint compatible with OpenAI's chat completions API, and
  prints the answer with numbered sources, or every record with `--json`. The agent
  sends a blocked answer back with a hint for each failed number, at most twice, and
  stops at its limits of model calls, tool calls, tokens, time and cost.
- An evaluation suite as an Inspect task, with question sets v1, v2 and v3 in
  Norwegian and English, reference plans whose expected numbers come from the day's
  data, a deterministic scorer and a run report. The published runs are in
  `evals/results/`; the latest, with DeepSeek's `deepseek-flash`, answered 40 of 44
  questions of set v1 and 40 of 43 of set v3 correctly.
- Specifications 0001 to 0005 and decision records 0001 to 0012, CI with type checks
  and tests on Python 3.13 and 3.14, recorded SSB responses for the tests, a nightly
  live test against SSB's API, and a release workflow that publishes to PyPI with
  trusted publishing.

[Unreleased]: https://github.com/danielnorlan/opencited/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/danielnorlan/opencited/releases/tag/v0.1.0
