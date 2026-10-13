# 0011. Snapshots and exact arithmetic

- Status: Proposed
- Date: 2026-10-13

## Context

[ADR 0004](0004-evidence-gate.md) runs calculations in DuckDB over stored snapshots,
and [ADR 0009](0009-exact-decimals.md) keeps values exact from the source to the
answer. Tests with DuckDB 1.5.6 on 2026-10-13 showed where the two meet:

- `/` and `//` on `DECIMAL` values return `DOUBLE`, and so does `AVG`.
- `ROUND` on a `DECIMAL` is exact and rounds ties away from zero.
- `SUM` on `DECIMAL` values is exact, but it returned a value of 39 significant digits
  typed `DECIMAL(38,6)` without an error.
- A Python `Decimal` with more than 38 significant digits is bound as `DOUBLE`
  without an error, and a bare `DECIMAL` type means `DECIMAL(18,3)`
  ([DuckDB](https://duckdb.org/docs/current/sql/data_types/numeric)).
- While one process has a database file open for writing, no other process can open
  it.
- Parquet files keep `DECIMAL(38,6)` values exact.

The MCP server writes snapshots and the calculation engine reads them, possibly from
another process.

## Decision

1. A snapshot is written once: the raw response body, the provenance record and the
   parsed values as a Parquet file with explicit `DECIMAL(38, s)` columns. It is
   written to a temporary directory and renamed into place when complete, and it is
   never changed.
2. The engine loads snapshots into an in-memory DuckDB database, which selects and
   sums values. Generated SQL binds values as text cast to an explicit decimal type.
3. Division, means, percentages and shares are computed on exact rational numbers in
   Python and rounded once, with ties away from zero, to the decimals that the plan
   states.
4. A result with more than 38 significant digits, or of a floating-point type, is an
   error.
5. After loading the snapshots, the engine turns off external access in DuckDB and
   locks its configuration before it runs generated SQL.

This record amends decision 2 of ADR 0004: DuckDB runs the selections and the sums,
and the result shows every arithmetic step next to the SQL.

## Alternatives considered

- One DuckDB database file for all snapshots. Only one process could write to it.
- Division in DuckDB with a cast of the result. The value passes through `DOUBLE`
  and keeps about 15 to 17 significant digits.
- Python's `decimal` module with a fixed precision for division. It rounds
  intermediate results, which rational numbers avoid.

## Consequences

- Two places compute: DuckDB for selections and sums, Python for the arithmetic.
  Tests compare both with exact rational arithmetic.
- A reader sees each calculation as SQL for what was selected and arithmetic with the
  numbers filled in.
- Snapshots accumulate on disk. A command to remove old ones comes later.
