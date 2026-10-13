# 0002. Python, uv and strict type checking

- Status: Accepted
- Date: 2026-10-13

## Context

OpenCited needs an MCP server, data processing, an agent, evaluation tooling and,
later, model fine-tuning. One maintainer works on it in limited time, so the
toolchain should catch mistakes early and explain them precisely.

## Decision

- Python 3.13 or newer, tested on 3.13 and 3.14.
- uv for environments, the lockfile and builds, with the `uv_build` backend.
- Ruff for linting and formatting, Pyright in strict mode for types and pytest for
  tests.
- Pydantic models where data enters the system, added with the first such
  boundary.

## Alternatives considered

- TypeScript. Strong for MCP servers and web work, with weaker libraries for data
  analysis, evaluation and fine-tuning. The demo site in M4 will use TypeScript.
- Go. Fast and simple to deploy, with a small ecosystem for machine learning and
  evaluation.
- mypy. Pyright is faster, and its strict mode also reports values of unknown type
  from libraries. Either would work.

## Consequences

- One language covers the server, the agent, the evaluations and fine-tuning.
- Python is slower than Go for heavy data work. DuckDB does the calculations
  ([ADR 0004](0004-evidence-gate.md)), so this matters little.
- Strict typing slows the first draft of each module and catches type errors
  before the tests run.
