# Spec 0005: Evaluation suite and the first question set

| Field | Value |
| --- | --- |
| Status | Approved |
| Milestone | M2 |
| Owner | Daniel Norlan |
| Created | 2026-10-13 |

## Goal

A repeatable measurement of how often the agent answers correctly, refuses when it
should and is blocked by the evidence gate. The questions are versioned, and the
calculation engine computes their expected answers.

## Background

- [ADR 0004](../adr/0004-evidence-gate.md) expects the gate to refuse some correct
  answers and leaves it to the evaluation suite to measure how often.
- [ADR 0012](../adr/0012-inspect-evaluations.md) runs the suite as Inspect tasks,
  with the agent inside the agent bridge, a deterministic scorer of our own and
  Inspect pinned in a separate dependency group.
- Inspect's documentation describes limits per question and none for a whole run,
  and its logs hold full prompts and answers.

## Users

- The maintainer, who publishes results.
- Anyone who wants to run the suite against a model of their choice.

## Requirements

### Questions

1. The questions are a JSONL file, `evals/questions/v1.jsonl` for the first set,
   with a set version. Each question has an ID, a language (`no` or `en`), the
   question text, a category and the expected outcome.
2. The categories are lookup, change, share, sum, ranking, false premise,
   unanswerable and out of scope.
3. For a question with an answer, false premises included, the expected outcome is
   a reference plan for the calculation engine
   ([spec 0002](0002-snapshots-and-calculation.md)), the tables and selections it
   reads, and the number of decimals that the answer must show at least. A ranking
   question also expects a label. For an unanswerable or out of scope question, the
   expected outcome is a refusal.
4. Expected numbers are never written by hand. A run executes each reference plan on
   data fetched during the same run, so a revision at SSB changes the expected
   answer and the agent's data together.
5. The first set has at least 40 questions in Norwegian and English: at least 5 in
   each of lookup, change, share, sum and ranking, at least 5 with a false premise,
   and at least 10 that should be refused.

### Scoring

1. The scorer is deterministic and reads numbers with the rules of the evidence gate
   ([spec 0003](0003-evidence-gate.md)).
2. A question with an answer is correct when the agent releases an answer, the answer
   passes the gate again when the scorer checks it, and it contains each expected
   number at the required decimals or more under the gate's rounding rule. A ranking
   answer must also contain the expected label as the snapshot gives it in the
   language of the question, compared without regard to case.
3. A question that should be refused is correct when the agent refuses. The reason is
   recorded.
4. The report gives counts and rates of correct answers by category and language,
   refusals of questions that have an answer, answers to questions that should be
   refused, gate blocks on the first attempt and retries, and the tokens, cost and
   time per question.

### Runs

1. The suite runs as an Inspect task with the agent inside the agent bridge, so the
   model under evaluation is set with Inspect's model option.
2. Each question has the agent's limits ([spec 0004](0004-agent.md)) and Inspect's
   limits on tokens, time and cost. Inspect's limits are set above the agent's, so
   the agent stops first and refuses with a reason. The cost limit per question
   times the number of questions stays within the budget of the run, so a run needs
   model prices.
3. Each run records the question set version, the commit, the model and the
   endpoint without the key, the model settings and prices, the date, the snapshot
   IDs and the result of every question. The model is pinned by the name that the
   endpoint reports in its responses.
4. The Inspect task lives in `evals/` ([ADR 0012](../adr/0012-inspect-evaluations.md)).
   Inspect logs stay out of git, and the report of every published run is committed
   under `evals/results/`.
5. A run sends no new question during the periods that
   [ADR 0007](../adr/0007-ssb-pxwebapi-v2.md) keeps scheduled jobs away from, in
   Norwegian time, and the report lists the questions it did not run.

## Verification

1. Unit tests for the scorer with example answers built from recorded fixture
   values, each with the verdict it must get. Answers from published runs join them.
2. Every reference plan runs in CI against recorded fixtures, so the fixtures cover
   every table and selection that the plans read. The question file is checked
   against its schema.
3. The suite with a model runs only against a real endpoint, outside the default
   test run.

## Acceptance criteria

- [ ] The first set has at least 40 questions in the categories above, and every
      reference plan runs against recorded fixtures in CI.
- [ ] No expected number is written by hand.
- [ ] The same answers and data give the same scores.
- [ ] A run report holds everything listed under Runs and no API key.
- [ ] One complete run against a pinned hosted model is published under
      `evals/results/`, within its cost limit.
- [ ] Inspect logs are ignored by git.

## Out of scope

- The benchmark of 500 or more questions on the roadmap.
- A language model as the judge (ADR 0004).
- Comparisons between models and the public results page (M3 and M4).

## Tasks

1. Question schema, the first set and its reference plans.
2. The scorer.
3. The Inspect task, the agent bridge, the limits and the run report.
4. The first published run.
