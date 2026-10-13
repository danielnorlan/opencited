# 0004. Evidence gate and deterministic calculation

- Status: Accepted
- Date: 2026-10-13

## Context

Language models misstate numbers and invent citations. OpenCited is useful only if
a reader can trust that every number in an answer traces back to a source.

## Decision

1. The language model never writes a number from its own knowledge. Numbers come
   from source tools, which attach a provenance record, or from the calculation
   engine.
2. Calculations run in DuckDB over stored snapshots of the retrieved data. The
   model writes a typed calculation plan, and the engine turns it into SQL and runs
   it with exact decimals. The SQL is shown to the reader as the calculation.
3. Before an answer is released, a deterministic gate reads every number in it and
   matches each one to a provenance record or a calculation result. An answer with
   an unmatched number is blocked. The agent may retry within a budget, and after
   that it refuses with a reason.

## Alternatives considered

- A language model as the judge of faithfulness. It is not deterministic and can be
  fooled, so it cannot be the final check.
- A Python sandbox for calculations. More flexible, with a larger attack surface,
  and its code is harder to present as a calculation.
- Trusting the model's arithmetic. That contradicts the purpose of the project.

## Consequences

- Some correct answers will be refused. The evaluation suite measures how often.
- Number formats need explicit rules, for example decimal commas in Norwegian text
  and rounding in prose.
- The first version covers numbers only. Text claims from sources such as health
  guidance need citations to exact passages and a separate decision.
