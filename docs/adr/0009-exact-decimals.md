# 0009. Exact decimals from the source to the answer

- Status: Proposed
- Date: 2026-10-13

## Context

Spec 0001 requires that values never pass through floating point. Each step on the
way can break that rule without an error:

- JSON numbers parsed by Pydantic into `Decimal` lose digits, because they pass
  through `float`: `0.12345678901234567890123` became `0.12345678901234568` in a
  test with Pydantic 2.14.1. JSON strings keep every digit.
- The JSON schema that Pydantic generates for a `Decimal` field in validation mode
  allows numbers as well as strings, which tells an MCP client that a float is
  acceptable.
- DuckDB keeps `DECIMAL` exact for addition, subtraction and multiplication up to 38
  digits, returns `DOUBLE` for division, and binds a Python `Decimal` with more than
  38 digits as `DOUBLE` without an error
  ([DuckDB](https://duckdb.org/docs/current/sql/data_types/numeric)).

## Decision

1. Source responses are parsed from the raw bytes with
   `json.loads(parse_float=Decimal, parse_int=Decimal)`. The same bytes are hashed
   for the provenance record.
2. Inside the package values are `Decimal`. Validated models never parse JSON
   numbers themselves.
3. At the MCP boundary values are decimal strings, and the published output schema
   declares them as strings with a decimal pattern.
4. In M2 the calculation engine casts every value to an explicit `DECIMAL` type,
   makes division and rounding explicit steps of the plan, and rejects any result
   column of a floating-point type.
5. A test walks every tool result and fails if it finds a `float`.

## Alternatives considered

- JSON numbers at the MCP boundary. Most clients parse them as floating point.
- Floating point with rounding at the end. It contradicts the purpose of the
  project.

## Consequences

- Clients that want numbers must convert the strings, and the tool descriptions
  say so.
- Values with more than 38 digits cannot enter a DuckDB calculation. The engine
  reports them as an error.
