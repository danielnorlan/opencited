# 0012. Evaluations with Inspect AI

- Status: Proposed
- Date: 2026-10-13

## Context

The architecture page planned the evaluation suite on Inspect AI. The facts below
were checked on 2026-10-13 with `inspect-ai` 0.3.279 and `openai` 3.29.0.

- Inspect reads datasets from JSONL, runs custom solvers and scorers, and limits each
  sample by tokens, messages, time and cost. The cost limit needs model prices.
- Its documentation describes the limits as checks on model calls that pass through
  its own model layer. Its agent bridge sends the calls that an agent makes through
  the `openai` SDK to the model under evaluation. A test run confirmed the bridge,
  and Inspect recorded the token use.
- The documentation describes limits per sample and none for a whole run. Inspect's
  metrics are floating-point numbers.
- With the `openai` SDK it installs about 85 packages into an empty environment,
  among them `numpy`, `boto3` and `fastapi`. New versions appear every few days.
- Its logs hold the full prompts and answers.

## Decision

1. The evaluation suite is a set of Inspect tasks. The agent from
   [ADR 0010](0010-agent-loop.md) runs inside the agent bridge, so the model under
   evaluation, its limits and its token counts are Inspect's.
2. The scorer is our own and deterministic. It compares numbers as decimals with the
   rounding rule of the evidence gate. Inspect's metrics only count answers, and
   they never carry a number from a source.
3. Inspect is pinned to an exact version in a separate dependency group, `evals`,
   with its own CI job. The package does not depend on it.
4. The cost limit per question is set so that the limit times the number of
   questions stays within the budget of the run.
5. Logs stay out of git. The report of each published run is committed.

## Alternatives considered

- A runner of our own. It would need no heavy dependencies, but it would have no log
  viewer and no shared log format, and every model provider would need our own
  client.
- A language model as the judge. ADR 0004 rules it out as the final check.

## Consequences

- Anyone with a model that Inspect supports can run the suite against it.
- CI gains one job, and updates for the `evals` group arrive often.
- An evaluation run needs the network for SSB and for the model.
