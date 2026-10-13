# Spec 0001: SSB source and MCP server with provenance

| Field | Value |
| --- | --- |
| Status | Draft |
| Milestone | M1 |
| Owner | Daniel Norlan |
| Created | 2026-10-13 |

## Goal

An MCP server that lets MCP clients search Statistics Norway's StatBank, read
table metadata and retrieve data. Every data result carries a provenance record, so
each value can be traced to its table, its selection, the API request and the time
it was retrieved.

## Background

- The evidence gate in M2 ([ADR 0004](../adr/0004-evidence-gate.md)) needs
  provenance for every number. This specification creates it at the source.
- Community MCP servers for SSB exist. This one differs by attaching a provenance
  record to every data result.
- SSB serves StatBank through PxWebApi. Tables have five-digit IDs. GET requests
  are limited to about 2,500 characters, and longer queries use POST
  ([SSB](https://ssb.no/en/api/pxwebapi/pxwebapi-2.0-beta)).

## Users

- MCP clients that support the 2026-07-28 revision, such as Claude Code.
- The OpenCited agent in M2.

## Requirements

### Tools

| Tool | Input | Result |
| --- | --- | --- |
| `search_tables` | `query`, `language` (`no` or `en`, default `no`), `limit` (1 to 50, default 10) | Matching tables with ID, title, first and last period, last update and a link |
| `get_table_metadata` | `table_id`, `language` | Title, unit, dimensions with codes and labels, notes, last update and a link |
| `query_table` | `table_id`, `selection`, `language` | Values with their dimension codes, and a provenance record |

1. Tool names and result shapes carry no source-specific naming, so later
   statistics sources fit behind the same tools ([ADR 0003](../adr/0003-mcp-2026-07-28.md)).
2. `table_id` must be five digits. Invalid input is rejected before any request is
   sent.
3. `selection` maps each dimension code to a list of value codes. Wildcards and
   period expressions are supported where the API supports them (open question 1).
4. Every tool declares an output schema and returns structured content.
5. The order of tools in `tools/list` is fixed.

### Values

1. Values are kept exactly as delivered, as decimal strings or `Decimal`. They never
   pass through floating point.
2. SSB's symbols for missing, confidential or not applicable values are kept as
   delivered, with the explanation from the API when there is one. They are never
   turned into numbers.

### Provenance record

Each `query_table` result has one record:

| Field | Content |
| --- | --- |
| `source_id` | `ssb` |
| `publisher` | `Statistics Norway (SSB)` |
| `license` | SPDX identifier, `CC-BY-4.0` |
| `table_id` | Five-digit table ID |
| `table_title` | Title in the requested language |
| `table_url` | Link to the table in StatBank |
| `table_updated` | Last update of the table, as reported by the API |
| `request` | Method, URL and body of the API request |
| `retrieved_at` | UTC time of the request, ISO 8601 |
| `response_sha256` | SHA-256 of the raw response body |

### Limits and failures

1. A selection above 5,000 cells is rejected before any data request. The error
   states the cell count and how to narrow the selection.
2. Requests stay within the rate limits that the API publishes in its
   configuration endpoint.
3. Metadata and data responses are cached. A cached response keeps its original
   `retrieved_at` and `response_sha256`, and the result says that it came from the
   cache.
4. When the API cannot be reached, the tool returns an error that says so. It
   returns cached data only when the result is marked as cached.

### Transport and packaging

1. MCP specification 2026-07-28 with the official Python SDK
   ([ADR 0003](../adr/0003-mcp-2026-07-28.md)).
2. stdio and Streamable HTTP.
3. `opencited mcp` starts the server on stdio. An option selects Streamable HTTP.
4. Published to PyPI with trusted publishing, and listed in the MCP Registry.

## Verification

1. Unit tests for input validation, selection expansion, cell counting and value
   parsing, with property-based tests (Hypothesis) for parsing.
2. Contract tests against recorded API responses in `tests/fixtures/ssb/`. A script
   records the fixtures from the live API and puts the recording date in the file
   name. Fixtures are never written by hand.
3. A live smoke test, marked `live`, runs every night in CI against the real API.
4. An end-to-end test starts the server and calls every tool through an MCP client,
   over both transports.

## Acceptance criteria

- [ ] Every tool works over stdio and Streamable HTTP in the end-to-end test.
- [ ] For three tables chosen in task 1, one of them with missing or confidential
      values, `query_table` returns the same values as the recorded raw response,
      cell for cell.
- [ ] Every `query_table` result has a complete provenance record, and
      `response_sha256` matches the raw response body.
- [ ] Invalid table IDs and oversized selections are rejected without any request to
      the API.
- [ ] A test fails if any value in a result is a `float`.
- [ ] `uvx opencited mcp` starts the server from a clean environment, checked in CI
      against the built package.
- [ ] The server is tested by hand with Claude Code and one other MCP client, and
      the README shows how to connect both.
- [ ] SSB attribution is in the README and in `NOTICE` when the first fixtures are
      added.

## Out of scope

- Calculations, comparisons and written answers (M2).
- Semantic or vector search over tables (M3). `search_tables` uses the API's own
  search.
- Authentication. The server exposes public data only.
- Other sources.

## Open questions for task 1

1. Which PxWebApi version SSB runs in production, and which selection expressions it
   supports. SSB's documentation describes version 2 as a beta with planned downtime
   on weekday mornings and at weekends, while a third-party client uses a version 2
   address without the beta suffix. The answer goes into a new ADR.
2. The rate limits published by the configuration endpoint.
3. How symbols and their explanations appear in data and metadata responses.
4. Whether CC BY 4.0 covers data from the API, to be confirmed with SSB in writing.

## Tasks

1. Spike: answer the open questions, choose three fixture tables, write the
   recording script and record the fixtures.
2. API client with typed models for search, metadata and data, and contract tests.
3. Values and provenance: parsing, symbols, the provenance record and
   property-based tests.
4. MCP server: tools, validation, limits, both transports and the end-to-end test.
5. Caching and rate limiting.
6. Packaging and release: the command line, trusted publishing to PyPI,
   `server.json` for the MCP Registry and client setup in the README.
7. Nightly live smoke test.
