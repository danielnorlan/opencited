# Spec 0002: Snapshots and the calculation engine

| Field | Value |
| --- | --- |
| Status | Draft |
| Milestone | M2 |
| Owner | Daniel Norlan |
| Created | 2026-10-13 |

## Goal

Every value that the agent uses is kept in a snapshot that never changes, and every
number that the agent derives comes from a typed calculation plan. The engine runs
the plan with exact arithmetic and returns each step, so a reader can repeat the
calculation.

## Background

- [ADR 0004](../adr/0004-evidence-gate.md) runs calculations over stored snapshots,
  from a plan that the model writes.
- [ADR 0009](../adr/0009-exact-decimals.md) keeps values as decimals from the source
  to the answer.
- [ADR 0011](../adr/0011-snapshots-and-exact-arithmetic.md) stores snapshots as
  write-once Parquet files, lets DuckDB select and sum, and computes division,
  means and rounding on exact rational numbers.
- The provenance record from [spec 0001](0001-ssb-mcp-server.md) has a
  `response_sha256`, which identifies a snapshot.

## Users

- The agent ([spec 0004](0004-agent.md)), through a `calculate` tool.
- The evaluation suite ([spec 0005](0005-evaluation-suite.md)), which computes its
  expected answers with the same engine.
- Readers, who see each step of a calculation.

## Requirements

### Snapshots

1. When a snapshot directory is configured, the MCP server writes a snapshot for
   every `query_table` result it returns, including results from the cache. The
   agent always configures one.
2. A snapshot holds the raw response body, the provenance record and the
   observations. Its ID is the `response_sha256` of the provenance record.
3. The observations are a Parquet file with one column per dimension code, the value
   as written in the response, the value as `DECIMAL(38, s)` where `s` is the largest
   scale among the values, the symbol for a missing value, and the unit of the
   content.
4. Units come from the table metadata that the server read for the same request. The
   snapshot stores the SHA-256 of that metadata response.
5. A snapshot is written to a temporary directory and renamed into place when it is
   complete. A written snapshot never changes, and writing the same response again
   leaves it as it is.
6. Reading a snapshot checks that the SHA-256 of the stored body equals its ID.

### Calculation plans

A plan is a list of steps. Each step has a name, an operation and inputs, which are
snapshot cells or the results of earlier steps.

| Operation | Inputs | Result |
| --- | --- | --- |
| `value` | A snapshot and one code for every dimension | The value of one cell |
| `sum` | A snapshot and one or more codes per dimension | The sum of the selected cells |
| `add`, `subtract`, `multiply` | Two results | The exact result |
| `divide` | Two results and a number of decimals | The quotient, rounded |
| `mean` | A snapshot, a selection and a number of decimals | The mean of the selected cells, rounded |
| `change_percent` | An old and a new result, and a number of decimals | (new − old) / old × 100, rounded |
| `share_percent` | A part and a total, and a number of decimals | part / total × 100, rounded |
| `rank` | A snapshot, a selection, the dimension to rank by and the order | The cells with the highest or lowest value, with their codes and labels, ties included |

1. Plans are Pydantic models in strict mode, and the `calculate` tool publishes
   their JSON schema.
2. A plan is checked before it runs. Every snapshot must exist, every code must exist
   in its dimension and every input must come from an earlier step. A plan has at
   most 20 steps, and a step selects at most 5,000 cells.
3. A step fails when it reads a missing value, divides by zero, gives a result with
   more than 38 significant digits, or combines values with different units in
   `sum`, `add` or `subtract`.
4. A step that states a number of decimals rounds once, with ties away from zero.
   Later steps use the rounded value. The step also keeps its exact result as a
   fraction.
5. The engine opens an in-memory DuckDB database, loads the snapshots that the plan
   names, turns off external access and locks the configuration. Only then does it
   run the SQL that it generates. The model never writes SQL.
6. Generated SQL binds codes and values as parameters. Values are bound as text and
   cast to an explicit `DECIMAL(38, s)`.
7. A result column of a floating-point type is an error.

### Calculation results

1. A result lists every step with its operation, its inputs, the SQL it ran when it
   read a snapshot, its exact result, its rounded result as a decimal string and its
   unit.
2. A result names every snapshot it read, with the provenance record.
3. No value in a result is a `float`.
4. A readable form shows each step as SQL or as arithmetic with the numbers filled
   in.

## Verification

1. Unit tests for every operation, every plan check and every failure.
2. Property-based tests (Hypothesis) that compare every operation with exact
   rational arithmetic on random decimals of up to 38 digits.
3. Snapshot tests on the recorded fixtures from spec 0001: the observations equal the
   raw response cell for cell, and the stored body hashes to the snapshot ID.
4. A test that generated SQL cannot read a file once the snapshots are loaded.
5. A test that fails if any value in a calculation result is a `float`.

## Acceptance criteria

- [ ] Snapshots of the spec 0001 fixtures hold the same values as the raw responses,
      cell for cell, and each stored body hashes to its snapshot ID.
- [ ] Writing a snapshot twice gives one snapshot, and a stored snapshot never
      changes.
- [ ] Every operation gives the same result as exact rational arithmetic in
      property-based tests.
- [ ] Every rounding has ties away from zero and uses the decimals that the plan
      states.
- [ ] A plan that reads a missing value, mixes units, divides by zero, exceeds a
      limit or produces more than 38 significant digits fails with an error that
      names the step.
- [ ] No value in a calculation result is a `float`, and a floating-point result
      column is rejected.
- [ ] Every step that reads a snapshot shows its SQL, and that SQL cannot read
      files.

## Out of scope

- SQL written by the model or by users.
- Joins between tables. A step can still combine single results from different
  tables, for example to divide one by another.
- Forecasts, trends, seasonal adjustment and price adjustment.
- Removing old snapshots.

## Tasks

1. Snapshot store: writing, reading and the server option.
2. Plan models, plan checks and the schema of the `calculate` tool.
3. Operations, generated SQL and exact arithmetic.
4. Calculation results and their readable form.
