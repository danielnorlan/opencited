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
  the `openai` SDK to the model under evaluation. A test with Inspect's mock model
  sent one plain completion through the bridge, and Inspect recorded the token use.
  Tool calls and limits through the bridge were not part of that test.
- The documentation describes limits per sample and none for a whole run. Inspect's
  metrics are floating-point numbers.
- Installing `inspect-ai` 0.3.279 and `openai` 3.29.0 into an empty environment adds
  85 packages, among them `numpy`, `boto3`, `fastapi` and `httpx`, next to the
  `httpx2` that the MCP SDK uses. New versions of Inspect appear every few days.
- Its logs hold the full prompts and answers.

## Decision

1. The evaluation suite is a set of Inspect tasks. The agent from
   [ADR 0010](0010-agent-loop.md) runs inside the agent bridge, so the model under
   evaluation, its limits and its token counts are Inspect's.
2. The scorer is our own and deterministic. It compares numbers as decimals with the
   rounding rule of the evidence gate. Inspect's metrics only count answers, and
   they never carry a number from a source.
3. Inspect is pinned to an exact version in a separate dependency group, `evals`.
   The Inspect task lives in the top-level `evals/` directory, which a CI job of its
   own type-checks and tests with that group. The scorer and the runner for
   reference plans live in the package without importing Inspect, so the main test
   job covers them.
4. The cost limit per question is set so that the limit times the number of
   questions stays within the budget of the run, so a run needs model prices.
   Inspect's limits are set above the agent's, so the agent stops first and refuses
   with a reason.
5. Logs stay out of git. The report of each published run is committed.

## Alternatives considered

- A runner of our own. It would need no heavy dependencies, but it would have no log
  viewer and no shared log format, and models without an OpenAI-compatible endpoint
  would be out of reach.
- A language model as the judge. ADR 0004 rules it out as the final check.

## Consequences

- Anyone with a model that Inspect supports can run the suite against it.
- Published numbers measure the agent with its model calls going through Inspect's
  model layer. `opencited ask` calls the endpoint directly.
- The `evals` environment holds both `httpx` and `httpx2`.
- CI gains one job, and updates for the `evals` group arrive often.
- An evaluation run needs the network for SSB and for the model.
